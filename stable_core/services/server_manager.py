"""
Server Manager — управление VPN серверами через SSH.

Устанавливает awg-server, настраивает сплит-роутинг и мосты,
а также собирает системные метрики (CPU, RAM, Disk).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from functools import partial
import secrets

import paramiko

from database.models import Server

logger = logging.getLogger(__name__)

# ── AWG 2.0 Parameter Generation ─────────────────────────────────────

def generate_awg2_params(preset="default"):
    """Generate valid AWG 2.0 obfuscation parameters.

    Returns (params_dict, preset_name).
    Guarantees: H1-H4 ranges don't overlap, S1+56 != S2, safe for Windows client.
    """
    if preset == "mobile":
        jc = 3
        jmin = random.randint(30, 50)
        jmax = jmin + random.randint(20, 80)
    else:
        jc = random.randint(3, 6)
        jmin = random.randint(40, 89)
        jmax = jmin + random.randint(50, 250)

    s1 = random.randint(15, 150)
    s2 = random.randint(15, 150)
    while s1 + 56 == s2:
        s2 = random.randint(15, 150)
    s3 = random.randint(8, 55)
    s4 = random.randint(4, 27)

    def _gen_h_range(used_ranges):
        for _ in range(100):
            start = random.randint(100000, 2000000000)
            width = random.randint(100000, 100000000)
            end = start + width
            if end > 2147483647:
                continue
            if any(s <= end and e >= start for s, e in used_ranges):
                continue
            return "%d-%d" % (start, end)
        raise RuntimeError("Could not generate non-overlapping H range")

    used = []
    h1 = _gen_h_range(used)
    used.append(tuple(map(int, h1.split("-"))))
    h2 = _gen_h_range(used)
    used.append(tuple(map(int, h2.split("-"))))
    h3 = _gen_h_range(used)
    used.append(tuple(map(int, h3.split("-"))))
    h4 = _gen_h_range(used)

    params = {
        "Jc": jc, "Jmin": jmin, "Jmax": jmax,
        "S1": s1, "S2": s2, "S3": s3, "S4": s4,
        "H1": h1, "H2": h2, "H3": h3, "H4": h4,
        "I1": "<r 128>",
    }
    return params, preset


def awg2_params_to_env(params):
    """Convert AWG 2.0 params dict to bash env-export lines."""
    lines = []
    for key, val in params.items():
        lines.append("export AWG_%s='%s'" % (key.upper(), val))
    return "\n".join(lines)


def awg2_params_to_ini(params):
    """Convert AWG 2.0 params dict to [Interface] config lines."""
    lines = []
    for key, val in params.items():
        lines.append("%s = %s" % (key, val))
    return "\n".join(lines)


# ── IPv6 Dual-Stack Helpers ──────────────────────────────────────────

DEFAULT_IPV6_SUBNET = "fddd:2c4:2c4:2c4::/64"


def generate_ipv6_address(subnet, client_index):
    """Generate IPv6 ULA address. Server=::1, clients=::2, ::3, ..."""
    base = subnet.replace("/64", "").rstrip(":")
    return "%s::%d" % (base, client_index + 1)


def get_ipv6_allowed_ips(server_has_native_ipv6, subnet=None):
    """Build AllowedIPs IPv6 portion."""
    if subnet is None:
        subnet = DEFAULT_IPV6_SUBNET
    if server_has_native_ipv6:
        return "::/0"
    return subnet


class ServerManager:
    """Управление Linux-серверами через SSH."""

    # ── SSH Connection ───────────────────────────────────────────────

    @staticmethod
    def _get_ssh_client(server: Server) -> paramiko.SSHClient:
        """Создать и настроить SSH-клиент для сервера."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": server.host,
            "port": server.port,
            "username": server.ssh_user,
            "timeout": 15,
        }

        if server.ssh_key_path:
            connect_kwargs["key_filename"] = server.ssh_key_path
        elif server.ssh_password:
            connect_kwargs["password"] = server.ssh_password

        ssh.connect(**connect_kwargs)
        return ssh

    @staticmethod
    def _exec_command(ssh: paramiko.SSHClient, command: str) -> str:
        """Выполнить команду через SSH и вернуть stdout."""
        logger.debug(f"SSH Exec: {command}")
        _, stdout, stderr = ssh.exec_command(command, timeout=120) # Больше таймаут для установки
        output = stdout.read().decode("utf-8").strip()
        errors = stderr.read().decode("utf-8").strip()
        if errors and "warning" not in errors.lower() and "apt" not in errors.lower():
            logger.warning("SSH stderr: %s", errors)
        return output

    # ── Server Provisioning ──────────────────────────────────────────

    async def deploy_awg_server(self, server: Server, **kwargs) -> str:
        """Развернуть awg-server на Linux."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._deploy_awg_server_sync, server, kwargs),
        )

    def _deploy_awg_server_sync(self, server: Server, kwargs: dict) -> str:
        ssh = self._get_ssh_client(server)
        token = server.api_token
        if not token:
            token = secrets.token_hex(16)
            server.api_token = token

        if not server.awg_params:
            preset = kwargs.get("preset") or server.awg_preset or "default"
            params, preset_name = generate_awg2_params(preset)
            server.awg_params = json.dumps(params)
            server.awg_preset = preset_name

        awg_params = json.loads(server.awg_params) if server.awg_params else {}
        awg_ini = awg2_params_to_ini(awg_params)
        preset_val = server.awg_preset or "default"
        port_val = server.port or 39743
        ipv6_on = "true" if server.ipv6_enabled else "false"
        ipv6_sub = server.ipv6_subnet or DEFAULT_IPV6_SUBNET

        try:
            install_script = (
                "set -e\n"
                "export DEBIAN_FRONTEND=noninteractive\n"
                "apt update && apt install -y curl ipset iptables iproute2\n"
                "\n"
                "# 1. Install AWG 2.0 kernel module + tools via bivlked installer\n"
                "if ! lsmod | grep -q amneziawg; then\n"
                "    cd /tmp\n"
                "    curl -fsSL https://raw.githubusercontent.com/bivlked/amneziawg-installer/main/install_amneziawg.sh -o install_awg.sh\n"
                "    bash install_awg.sh --preset=%s --port=%s --yes --route-amnezia --no-tweaks\n"
                "fi\n"
                "\n"
                "# 2. Enable IP forwarding + IPv6\n"
                "sysctl -w net.ipv4.ip_forward=1\n"
                "echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-ipforward.conf\n"
                "IPV6_ON=%s\n"
                'IPV6_SUB=%s\n'
                'IPV6_IF=""\n'
                'if [ "$IPV6_ON" = "true" ]; then\n'
                '    IPV6_IF=$(ip -6 route show default 2>/dev/null | awk '"'"'{print $5}'"'"' | head -1)\n'
                '    if [ -n "$IPV6_IF" ]; then\n'
                '        sysctl -w net.ipv6.conf.all.forwarding=1\n'
                '        IPV6_ADDR=$(echo "$IPV6_SUB" | sed "s|/64|::1/64|")\n'
                '    fi\n'
                'fi\n'
                "\n"
                "# 3. Generate awg0.conf with AWG 2.0 params\n"
                "cat > /etc/amnezia/amneziawg/awg0.conf << 'AWGCONF'\n"
                "[Interface]\n"
                "PrivateKey = $(cat /root/awg/server_private.key)\n"
                "Address = 10.0.0.1/24\n"
                'if [ "$IPV6_ON" = "true" ] && [ -n "$IPV6_IF" ]; then\n'
                '    echo "Address = $IPV6_ADDR" >> /etc/amnezia/amneziawg/awg0.conf\n'
                'fi\n'
                "ListenPort = %s\n"
                "%s\n"
                "\n"
                "PostUp = iptables -I FORWARD -i %%i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE\n"
                "PostDown = iptables -D FORWARD -i %%i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE\n"
                'if [ "$IPV6_ON" = "true" ] && [ -n "$IPV6_IF" ]; then\n'
                '    echo "PostUp = ip6tables -I FORWARD -i %%i -j ACCEPT; ip6tables -t nat -A POSTROUTING -o $IPV6_IF -j MASQUERADE" >> /etc/amnezia/amneziawg/awg0.conf\n'
                '    echo "PostDown = ip6tables -D FORWARD -i %%i -j ACCEPT; ip6tables -t nat -D POSTROUTING -o $IPV6_IF -j MASQUERADE" >> /etc/amnezia/amneziawg/awg0.conf\n'
                'fi\n'
                "AWGCONF\n"
                "\n"
                "# 4. Hot-reload via syncconf\n"
                "awg syncconf awg0 || systemctl restart awg-quick@awg0\n"
                "\n"
                "# 5. REST API wrapper (awg-server with AWG 2.0 env)\n"
                'curl -fsSL https://github.com/stealthsurf-vpn/awg-server/releases/latest/download/awg-server-linux-$(uname -m | sed "s/x86_64/amd64/" | sed "s/aarch64/arm64/") -o /usr/local/bin/awg-server\n'
                "chmod +x /usr/local/bin/awg-server\n"
                "\n"
                "cat > /etc/systemd/system/awg-server.service << 'EOF'\n"
                "[Unit]\n"
                "Description=AmneziaWG REST API Server\n"
                "After=awg-quick@awg0.service\n"
                "Requires=awg-quick@awg0.service\n"
                "\n"
                "[Service]\n"
                "Type=simple\n"
                "ExecStart=/usr/local/bin/awg-server\n"
                "Restart=always\n"
                "RestartSec=5\n"
                "Environment=AWG_API_TOKEN=%s\n"
                "Environment=AWG_ADDRESS=10.0.0.1/24\n"
                "Environment=AWG_ENDPOINT=%s\n"
                "Environment=AWG_PORT=%s\n"
                "Environment=AWG_JC=%s\n"
                "Environment=AWG_JMIN=%s\n"
                "Environment=AWG_JMAX=%s\n"
                "Environment=AWG_S1=%s\n"
                "Environment=AWG_S2=%s\n"
                "Environment=AWG_S3=%s\n"
                "Environment=AWG_S4=%s\n"
                "Environment=AWG_H1=%s\n"
                "Environment=AWG_H2=%s\n"
                "Environment=AWG_H3=%s\n"
                "Environment=AWG_H4=%s\n"
                "Environment=AWG_I1=%s\n"
                "\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n"
                "EOF\n"
                "\n"
                "systemctl daemon-reload\n"
                "systemctl enable --now awg-server\n"
            ) % (
                preset_val, port_val,
                ipv6_on, ipv6_sub,
                port_val, awg_ini,
                token, server.host, str(port_val),
                str(awg_params.get("Jc", 5)),
                str(awg_params.get("Jmin", 50)),
                str(awg_params.get("Jmax", 1000)),
                str(awg_params.get("S1", 72)),
                str(awg_params.get("S2", 56)),
                str(awg_params.get("S3", 32)),
                str(awg_params.get("S4", 16)),
                str(awg_params.get("H1", "234567-345678")),
                str(awg_params.get("H2", "3456789-4567890")),
                str(awg_params.get("H3", "56789012-67890123")),
                str(awg_params.get("H4", "456789012-567890123")),
                str(awg_params.get("I1", "<r 128>")),
            )
            output = self._exec_command(ssh, install_script)
            logger.info("AWG 2.0 deployed on %s (preset=%s)", server.name, server.awg_preset)
            return output
        except Exception as e:
            logger.error("Deploy awg-server failed on %s: %s", server.name, e)
            raise
        finally:
            ssh.close()

    async def setup_split_routing(self, server: Server, tunnel_interface: str = "awg-bridge") -> str:
        """Настроить сплит-роутинг на RU сервере с помощью ipset и iptables."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._setup_split_routing_sync, server, tunnel_interface),
        )

    def _setup_split_routing_sync(self, server: Server, tunnel_interface: str) -> str:
        ssh = self._get_ssh_client(server)
        try:
            # 1. Скачиваем списки RU IP
            # 2. Создаем ipset и добавляем туда
            # 3. Маркируем трафик, кроме RU
            # 4. Направляем маркированный в таблицу моста
            script = f"""
            set -e
            apt install -y ipset iptables iproute2 curl wget

            # Create or flush ipset
            ipset create ru_ips hash:net || ipset flush ru_ips

            # Download RU subnets
            curl -sL https://raw.githubusercontent.com/herrbischoff/country-ip-blocks/master/ipv4/ru.cidr > /tmp/ru.cidr
            
            # Load into ipset efficiently
            sed -e "s/^/add ru_ips /" /tmp/ru.cidr > /tmp/ru_ipset.restore
            ipset restore -exist < /tmp/ru_ipset.restore || true

            # Table for bridge
            if ! grep -q "100 vpn_bridge" /etc/iproute2/rt_tables; then
                echo "100 vpn_bridge" >> /etc/iproute2/rt_tables
            fi

            # Add default route for marked packets to tunnel interface
            # Note: tunnel_interface (e.g. awg1) must exist, this might fail if not created yet.
            ip route add default dev {tunnel_interface} table vpn_bridge || true
            ip rule add fwmark 0x1 table vpn_bridge || true

            # Mangle rules for traffic from client interfaces (awg0)
            # Find client interface (default awg0)
            CLIENT_IF="awg0"
            iptables -t mangle -F PREROUTING || true
            iptables -t mangle -A PREROUTING -i $CLIENT_IF -m set --match-set ru_ips dst -j RETURN
            iptables -t mangle -A PREROUTING -i $CLIENT_IF -j MARK --set-mark 0x1

            # NAT MASQUERADE
            iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE || true
            iptables -t nat -A POSTROUTING -o ens3 -j MASQUERADE || true
            iptables -t nat -A POSTROUTING -o $CLIENT_IF -j MASQUERADE || true
            iptables -t nat -A POSTROUTING -o {tunnel_interface} -j MASQUERADE || true
            """
            output = self._exec_command(ssh, script)
            logger.info("Сплит-роутинг настроен на %s", server.name)
            return output
        except Exception as e:
            logger.error("Ошибка сплит-роутинга на %s: %s", server.name, e)
            raise
        finally:
            ssh.close()

    # ── Server Status ─────────────────────────────────────────────────

    async def get_server_status(self, server: Server) -> dict:
        """Quick health check - runs awg show via SSH."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._get_server_status_sync, server),
        )

    def _get_server_status_sync(self, server: Server) -> dict:
        ssh = None
        try:
            ssh = self._get_ssh_client(server)
            output = self._exec_command(ssh, "awg show 2>/dev/null || echo 'OFFLINE'")
            if "OFFLINE" in output or not output.strip():
                return {"status": "offline", "peers": 0}
            peer_count = output.count("peer:")
            return {"status": "online", "peers": peer_count, "raw": output}
        except Exception as e:
            return {"status": "offline", "error": str(e)}
        finally:
            if ssh:
                ssh.close()

    # ── Monitoring ────────────────────────────────────────────────────

    async def get_cpu_load(self, server: Server) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._get_cpu_load_sync, server),
        )

    def _get_cpu_load_sync(self, server: Server) -> dict:
        ssh = self._get_ssh_client(server)
        try:
            output = self._exec_command(ssh, "uptime")
            return {"raw": output, "online": True}
        except Exception as e:
            return {"online": False, "error": str(e)}
        finally:
            ssh.close()

    async def get_memory_usage(self, server: Server) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._get_memory_usage_sync, server),
        )

    def _get_memory_usage_sync(self, server: Server) -> dict:
        ssh = self._get_ssh_client(server)
        try:
            output = self._exec_command(ssh, "free -m")
            lines = output.split("\n")
            for line in lines:
                if line.startswith("Mem:"):
                    parts = line.split()
                    total, used, free = int(parts[1]), int(parts[2]), int(parts[3])
                    percent = round(used / total * 100, 1) if total > 0 else 0
                    return {
                        "online": True, "total_mb": total,
                        "used_mb": used, "free_mb": free, "percent": percent,
                    }
            return {"online": True, "raw": output}
        except Exception as e:
            return {"online": False, "error": str(e)}
        finally:
            ssh.close()

    async def get_disk_usage(self, server: Server) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._get_disk_usage_sync, server),
        )

    def _get_disk_usage_sync(self, server: Server) -> dict:
        ssh = self._get_ssh_client(server)
        try:
            output = self._exec_command(ssh, "df -h / | tail -1")
            parts = output.split()
            if len(parts) >= 5:
                used_percent = int(parts[4].replace("%", ""))
                return {
                    "online": True, "size": parts[1],
                    "used": parts[2], "available": parts[3],
                    "used_percent": used_percent,
                }
            return {"online": True, "raw": output}
        except Exception as e:
            return {"online": False, "error": str(e)}
        finally:
            ssh.close()

    async def get_network_stats(self, server: Server) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._get_network_stats_sync, server),
        )

    def _get_network_stats_sync(self, server: Server) -> dict:
        ssh = self._get_ssh_client(server)
        try:
            output = self._exec_command(
                ssh, "cat /proc/net/dev | grep -E 'eth0|ens' | head -1",
            )
            parts = output.split()
            if len(parts) >= 10:
                return {
                    "online": True, "interface": parts[0].rstrip(":"),
                    "rx_bytes": int(parts[1]), "tx_bytes": int(parts[9]),
                }
            return {"online": True, "raw": output}
        except Exception as e:
            return {"online": False, "error": str(e)}
        finally:
            ssh.close()

    async def get_full_monitoring(self, server: Server) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._get_full_monitoring_sync, server),
        )

    def _get_full_monitoring_sync(self, server: Server) -> dict:
        ssh = self._get_ssh_client(server)
        try:
            commands = {
                "cpu": "uptime",
                "memory": "free -m | grep 'Mem:'",
                "disk": "df -h / | tail -1",
            }
            result = {"online": True}
            for key, cmd in commands.items():
                try:
                    result[key] = self._exec_command(ssh, cmd).strip()
                except Exception as e:
                    result[key] = f"ERROR: {e}"
            return result
        except Exception as e:
            return {"online": False, "error": str(e)}
        finally:
            ssh.close()

    # ── Cascade Deployment (Russia VPS -> Foreign VPS) ────────────────

    async def deploy_tunnel_endpoint(self, foreign_server, local_subnet="10.10.0.1/24",
                                     listen_port=0, preset="default"):
        """Setup foreign server as AWG 2.0 tunnel endpoint."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._deploy_tunnel_endpoint_sync,
                          foreign_server, local_subnet, listen_port, preset),
        )

    def _deploy_tunnel_endpoint_sync(self, foreign, local_subnet, listen_port, preset):
        ssh = self._get_ssh_client(foreign)
        if not listen_port:
            listen_port = random.randint(30000, 50000)

        params, preset_name = generate_awg2_params(preset)
        awg_ini = awg2_params_to_ini(params)

        server_key = self._exec_command(
            ssh, "cat /root/awg/server_private.key 2>/dev/null || echo 'NEED_INSTALL'",
        )
        if "NEED_INSTALL" in server_key:
            self._exec_command(ssh, (
                "set -e\n"
                "export DEBIAN_FRONTEND=noninteractive\n"
                "apt update && apt install -y curl\n"
                "cd /tmp\n"
                "curl -fsSL https://raw.githubusercontent.com/bivlked/amneziawg-installer/main/install_amneziawg.sh -o install_awg.sh\n"
                "bash install_awg.sh --preset=%s --port=%s --yes --route-amnezia --no-tweaks\n"
            ) % (preset_name, listen_port))
            server_key = self._exec_command(ssh, "cat /root/awg/server_private.key").strip()

        server_pub = self._exec_command(ssh, "echo '%s' | awg pubkey" % server_key).strip()

        try:
            tun_conf = (
                "mkdir -p /etc/amnezia/amneziawg/\n"
                "cat > /etc/amnezia/amneziawg/awg0.conf << 'TUNEOF'\n"
                "[Interface]\n"
                "PrivateKey = %s\n"
                "Address = %s\n"
                "ListenPort = %s\n"
                "%s\n"
                "TUNEOF\n"
                "systemctl enable --now awg-quick@awg0 2>/dev/null || awg-quick up awg0\n"
            ) % (server_key, local_subnet, listen_port, awg_ini)
            self._exec_command(ssh, tun_conf)
            logger.info("Tunnel endpoint on %s (port=%s)", foreign.name, listen_port)
            return {
                "server_public_key": server_pub,
                "endpoint": "%s:%s" % (foreign.host, listen_port),
                "local_subnet": local_subnet,
                "awg_params": params,
                "listen_port": listen_port,
            }
        finally:
            ssh.close()

    async def setup_tunnel_client(self, russian_server, foreign_endpoint,
                                  foreign_pubkey, local_subnet, awg_params,
                                  tunnel_interface="awg1"):
        """Setup Russian VPS as AWG client to foreign tunnel."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._setup_tunnel_client_sync,
                          russian_server, foreign_endpoint, foreign_pubkey,
                          local_subnet, awg_params, tunnel_interface),
        )

    def _setup_tunnel_client_sync(self, russian, foreign_endpoint, foreign_pubkey,
                                  local_subnet, awg_params, tunnel_interface):
        ssh = self._get_ssh_client(russian)
        client_key = self._exec_command(ssh, "awg genkey").strip()
        client_pub = self._exec_command(ssh, "echo '%s' | awg pubkey" % client_key).strip()
        awg_ini = awg2_params_to_ini(awg_params)
        base_ip = local_subnet.rsplit(".", 2)[0]
        client_ip = "%s.2/24" % base_ip

        try:
            tun_client = (
                "cat > /etc/amnezia/amneziawg/%s.conf << 'TUNCLIENTEOF'\n"
                "[Interface]\n"
                "PrivateKey = %s\n"
                "Address = %s\n"
                "MTU = 1280\n"
                "%s\n"
                "\n"
                "[Peer]\n"
                "PublicKey = %s\n"
                "Endpoint = %s\n"
                "AllowedIPs = 0.0.0.0/0\n"
                "PersistentKeepalive = 25\n"
                "TUNCLIENTEOF\n"
                "systemctl enable --now awg-quick@%s 2>/dev/null || awg-quick up %s\n"
            ) % (tunnel_interface, client_key, client_ip, awg_ini,
                 foreign_pubkey, foreign_endpoint, tunnel_interface, tunnel_interface)
            self._exec_command(ssh, tun_client)
            logger.info("Tunnel client %s on %s -> %s",
                        tunnel_interface, russian.name, foreign_endpoint)
            return client_pub
        finally:
            ssh.close()

    async def add_tunnel_peer(self, foreign_server, client_pubkey,
                              client_ip="10.10.0.2/32"):
        """Add Russian VPS as peer on foreign AWG server."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._add_tunnel_peer_sync,
                          foreign_server, client_pubkey, client_ip),
        )

    def _add_tunnel_peer_sync(self, foreign, client_pubkey, client_ip):
        ssh = self._get_ssh_client(foreign)
        try:
            self._exec_command(ssh, (
                "awg set awg0 peer %s allowed-ips %s\n"
                "awg syncconf awg0 2>/dev/null || systemctl restart awg-quick@awg0\n"
            ) % (client_pubkey, client_ip))
            logger.info("Peer %s added to tunnel on %s", client_ip, foreign.name)
            return "ok"
        finally:
            ssh.close()

    async def deploy_full_cascade(self, russian_server, foreign_server,
                                  tunnel_interface="awg1", routing_mode="full",
                                  preset="default"):
        """Full cascade: Russia VPS -> Foreign VPS -> Internet.

        Steps: 1) Foreign endpoint 2) Russian client 3) Peer add 4) Routing.
        """
        result = await self.deploy_tunnel_endpoint(
            foreign_server, local_subnet="10.10.0.1/24", preset=preset,
        )
        client_pub = await self.setup_tunnel_client(
            russian_server, foreign_endpoint=result["endpoint"],
            foreign_pubkey=result["server_public_key"],
            local_subnet=result["local_subnet"],
            awg_params=result["awg_params"], tunnel_interface=tunnel_interface,
        )
        await self.add_tunnel_peer(
            foreign_server, client_pubkey=client_pub, client_ip="10.10.0.2/32",
        )
        if routing_mode == "full":
            await self._setup_full_tunnel_routing(russian_server, tunnel_interface)
        else:
            await self.setup_split_routing(russian_server, tunnel_interface)

        foreign_server.awg_params = json.dumps(result["awg_params"])
        foreign_server.api_url = "http://%s:7777" % foreign_server.host

        return {
            "status": "deployed",
            "tunnel_interface": tunnel_interface,
            "endpoint": result["endpoint"],
            "foreign_pubkey": result["server_public_key"],
            "client_pubkey": client_pub,
            "routing_mode": routing_mode,
        }

    async def _setup_full_tunnel_routing(self, russian_server, tunnel_interface="awg1"):
        """Route ALL traffic from awg0 through tunnel to foreign."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self.__setup_full_tunnel_sync,
                          russian_server, tunnel_interface),
        )

    def __setup_full_tunnel_sync(self, russian, tunnel_if):
        ssh = self._get_ssh_client(russian)
        try:
            self._exec_command(ssh, (
                "set -e\n"
                "grep -q '200 tunnel_out' /etc/iproute2/rt_tables || echo '200 tunnel_out' >> /etc/iproute2/rt_tables\n"
                "ip route add default dev %s table tunnel_out 2>/dev/null || ip route replace default dev %s table tunnel_out\n"
                "ip rule add fwmark 0x1 table tunnel_out 2>/dev/null || true\n"
                "iptables -t mangle -F PREROUTING 2>/dev/null || true\n"
                "iptables -t mangle -A PREROUTING -i awg0 -j MARK --set-mark 0x1\n"
                "iptables -t nat -A POSTROUTING -o %s -j MASQUERADE 2>/dev/null || true\n"
                "iptables -t nat -A POSTROUTING -o awg0 -j MASQUERADE 2>/dev/null || true\n"
                "iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || true\n"
                "iptables -t nat -A POSTROUTING -o ens3 -j MASQUERADE 2>/dev/null || true\n"
            ) % (tunnel_if, tunnel_if, tunnel_if))
            logger.info("Full tunnel routing on %s via %s", russian.name, tunnel_if)
            return "ok"
        finally:
            ssh.close()
