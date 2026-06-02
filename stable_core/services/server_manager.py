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

def generate_awg2_params(preset: str = "default") -> tuple[dict, str]:
    """Generate valid AWG 2.0 obfuscation parameters.

    Returns (params_dict, preset_name).
    Guarantees: H1-H4 ranges don't overlap, S1+56 != S2, safe range for Windows client.
    """
    if preset == "mobile":
        jc = 3
        jmin = random.randint(30, 50)
        jmax = jmin + random.randint(20, 80)
    else:  # default
        jc = random.randint(3, 6)
        jmin = random.randint(40, 89)
        jmax = jmin + random.randint(50, 250)

    s1 = random.randint(15, 150)
    s2 = random.randint(15, 150)
    while s1 + 56 == s2:  # S1+56 must not equal S2
        s2 = random.randint(15, 150)
    s3 = random.randint(8, 55)
    s4 = random.randint(4, 27)

    # Generate non-overlapping H ranges (safe for Windows: < 2147483647)
    def _gen_h_range(used_ranges):
        for _ in range(100):
            start = random.randint(100000, 2000000000)
            width = random.randint(100000, 100000000)
            end = start + width
            if end > 2147483647:
                continue
            if any(s <= end and e >= start for s, e in used_ranges):
                continue
            return f"{start}-{end}"
        raise RuntimeError("Could not generate non-overlapping H range")

    used = []
    h1 = _gen_h_range(used); used.append(tuple(map(int, h1.split("-"))))
    h2 = _gen_h_range(used); used.append(tuple(map(int, h2.split("-"))))
    h3 = _gen_h_range(used); used.append(tuple(map(int, h3.split("-"))))
    h4 = _gen_h_range(used)

    i1 = "<r 128>"  # Default: 128 random bytes as first CPS packet

    params = {
        "Jc": jc, "Jmin": jmin, "Jmax": jmax,
        "S1": s1, "S2": s2, "S3": s3, "S4": s4,
        "H1": h1, "H2": h2, "H3": h3, "H4": h4,
        "I1": i1,
    }
    return params, preset


def awg2_params_to_env(params: dict) -> str:
    """Convert AWG 2.0 params dict to bash env-export lines."""
    lines = []
    for key, val in params.items():
        lines.append(f"export AWG_{key.upper()}='{val}'")
    return "\n".join(lines)


def awg2_params_to_ini(params: dict) -> str:
    """Convert AWG 2.0 params dict to [Interface] config lines for awg0.conf."""
    lines = []
    for key, val in params.items():
        lines.append(f"{key} = {val}")
    return "\n".join(lines)


# ── IPv6 Dual-Stack ──────────────────────────────────────────────────

DEFAULT_IPV6_SUBNET = "fddd:2c4:2c4:2c4::/64"


def generate_ipv6_address(subnet: str, client_index: int) -> str:
    """Generate IPv6 ULA address for client. Server=::1, clients=::2, ::3, ..."""
    base = subnet.replace("/64", "").rstrip(":")
    return f"{base}::{client_index + 1}"


def get_ipv6_allowed_ips(server_has_native_ipv6: bool, subnet: str = DEFAULT_IPV6_SUBNET) -> str:
    """Build AllowedIPs IPv6 portion based on server IPv6 capability."""
    if server_has_native_ipv6:
        return "::/0"
    return subnet


def awg2_ipv6_postup(ipv6_subnet: str, public_iface: str = "eth0") -> str:
    """Generate ip6tables PostUp/PostDown lines for IPv6 NAT."""
    return (
        f"PostUp = ip6tables -I FORWARD -i %i -j ACCEPT; "
        f"ip6tables -t nat -A POSTROUTING -o {public_iface} -j MASQUERADE\n"
        f"PostDown = ip6tables -D FORWARD -i %i -j ACCEPT; "
        f"ip6tables -t nat -D POSTROUTING -o {public_iface} -j MASQUERADE"
    )


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

        # Generate AWG 2.0 params if not already set
        if not server.awg_params:
            preset = kwargs.get("preset") or server.awg_preset or "default"
            params, preset_name = generate_awg2_params(preset)
            server.awg_params = json.dumps(params)
            server.awg_preset = preset_name

        awg_params = json.loads(server.awg_params) if server.awg_params else {}
        awg_env = awg2_params_to_env(awg_params)

        try:
            install_script = f"""
            set -e
            export DEBIAN_FRONTEND=noninteractive
            apt update && apt install -y build-essential git dkms linux-headers-$(uname -r) curl ipset iptables iproute2

            # 1. Install AWG 2.0 kernel module + tools via bivlked installer
            if ! lsmod | grep -q amneziawg; then
                rm -rf /tmp/awg-install
                mkdir -p /tmp/awg-install && cd /tmp/awg-install
                curl -fsSL https://raw.githubusercontent.com/bivlked/amneziawg-installer/main/install_amneziawg.sh -o install_awg.sh
                bash install_awg.sh --preset={server.awg_preset or 'default'} --port={server.port or 39743} --yes --route-amnezia --no-tweaks
            fi

            # 2. Enable IP forwarding
            sysctl -w net.ipv4.ip_forward=1
            echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-ipforward.conf

            # 3. Overwrite awg0.conf with AWG 2.0 + IPv6 dual-stack
            ipv6_subnet = server.ipv6_subnet or "fddd:2c4:2c4:2c4::/64"
            ipv6_addr = "$(echo '" + ipv6_subnet + """' | sed 's|/64|::1/64|')"
            ipv6_iface=""
            if server.ipv6_enabled:
                # Check if server has native IPv6
                ipv6_iface=$(ip -6 route show default 2>/dev/null | awk '{print $5}' | head -1)
                if [ -n "$ipv6_iface" ]; then
                    sysctl -w net.ipv6.conf.all.forwarding=1
                fi
            fi
            cat > /etc/amnezia/amneziawg/awg0.conf <<AWGCONF
[Interface]
PrivateKey = $(cat /root/awg/server_private.key)
Address = 10.0.0.1/24"""
            if server.ipv6_enabled:
                install_script += f"""
Address = {ipv6_addr}"""
            install_script += f"""
ListenPort = {server.port or 39743}
{awg2_params_to_ini(awg_params)}

PostUp = iptables -I FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE"""
            if server.ipv6_enabled:
                install_script += f"""
PostUp = ip6tables -I FORWARD -i %i -j ACCEPT; ip6tables -t nat -A POSTROUTING -o $ipv6_iface -j MASQUERADE
PostDown = ip6tables -D FORWARD -i %i -j ACCEPT; ip6tables -t nat -D POSTROUTING -o $ipv6_iface -j MASQUERADE"""
            install_script += """
AWGCONF"""

            # 4. Hot-reload via syncconf
            awg syncconf awg0 || systemctl restart awg-quick@awg0

            # 5. Set up REST API wrapper (awg-server with AWG 2.0 env)
            {awg_env}
            export AWG_API_TOKEN={token}
            export AWG_ADDRESS=10.0.0.1/24
            export AWG_ENDPOINT={server.host}
            export AWG_PORT={server.port or 39743}

            curl -fsSL https://github.com/stealthsurf-vpn/awg-server/releases/latest/download/awg-server-linux-$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/') -o /usr/local/bin/awg-server
            chmod +x /usr/local/bin/awg-server

            cat > /etc/systemd/system/awg-server.service <<EOF
[Unit]
Description=AmneziaWG REST API Server
After=awg-quick@awg0.service
Requires=awg-quick@awg0.service

[Service]
Type=simple
ExecStart=/usr/local/bin/awg-server
Restart=always
RestartSec=5
Environment=AWG_API_TOKEN={token}
Environment=AWG_ADDRESS=10.0.0.1/24
Environment=AWG_ENDPOINT={server.host}
Environment=AWG_PORT={server.port or 39743}
Environment=AWG_JC={awg_params.get('Jc', 5)}
Environment=AWG_JMIN={awg_params.get('Jmin', 50)}
Environment=AWG_JMAX={awg_params.get('Jmax', 1000)}
Environment=AWG_S1={awg_params.get('S1', 72)}
Environment=AWG_S2={awg_params.get('S2', 56)}
Environment=AWG_S3={awg_params.get('S3', 32)}
Environment=AWG_S4={awg_params.get('S4', 16)}
Environment=AWG_H1={awg_params.get('H1', '234567-345678')}
Environment=AWG_H2={awg_params.get('H2', '3456789-4567890')}
Environment=AWG_H3={awg_params.get('H3', '56789012-67890123')}
Environment=AWG_H4={awg_params.get('H4', '456789012-567890123')}
Environment=AWG_I1={awg_params.get('I1', '<r 128>')}

[Install]
WantedBy=multi-user.target
EOF

            systemctl daemon-reload
            systemctl enable --now awg-server
            """
            output = self._exec_command(ssh, install_script)
            logger.info("AWG 2.0 deployed on %s (preset=%s)", server.name, server.awg_preset)
            return output
        except Exception as e:
            logger.error("Ошибка развертывания awg-server на %s: %s", server.name, e)
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

    async def get_server_status(self, server: Server) -> dict:
        """Quick health check — runs 'awg show' via SSH."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._get_server_status_sync, server),
        )

    def _get_server_status_sync(self, server: Server) -> dict:
        ssh = self._get_ssh_client(server)
        try:
            output = self._exec_command(ssh, "awg show 2>/dev/null || echo 'OFFLINE'")
            if "OFFLINE" in output or not output.strip():
                return {"status": "offline", "peers": 0}
            peer_count = output.count("peer:")
            return {"status": "online", "peers": peer_count, "raw": output}
        except Exception as e:
            return {"status": "offline", "error": str(e)}
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

    # ── Cascade Deployment (Russia VPS → Foreign VPS) ────────────────

    async def deploy_tunnel_endpoint(
        self, foreign_server: Server, local_subnet: str = "10.10.0.1/24",
        listen_port: int = 0, preset: str = "default",
    ) -> dict:
        """Setup foreign server as AWG 2.0 tunnel endpoint."""
        import asyncio as aio
        loop = aio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._deploy_tunnel_endpoint_sync,
                          foreign_server, local_subnet, listen_port, preset),
        )

    def _deploy_tunnel_endpoint_sync(
        self, foreign: Server, local_subnet: str,
        listen_port: int, preset: str,
    ) -> dict:
        ssh = self._get_ssh_client(foreign)
        if not listen_port:
            listen_port = random.randint(30000, 50000)

        params, preset_name = generate_awg2_params(preset)
        awg_ini = awg2_params_to_ini(params)

        # Install AWG 2.0 if needed
        server_key = self._exec_command(ssh, "cat /root/awg/server_private.key 2>/dev/null || echo 'NEED_INSTALL'")
        if "NEED_INSTALL" in server_key:
            self._exec_command(ssh, f"""
set -e
export DEBIAN_FRONTEND=noninteractive
apt update && apt install -y curl
cd /tmp
curl -fsSL https://raw.githubusercontent.com/bivlked/amneziawg-installer/main/install_amneziawg.sh -o install_awg.sh
bash install_awg.sh --preset={preset_name} --port={listen_port} --yes --route-amnezia --no-tweaks
""")
            server_key = self._exec_command(ssh, "cat /root/awg/server_private.key").strip()

        server_pub = self._exec_command(ssh, f"echo '{server_key}' | awg pubkey").strip()

        try:
            self._exec_command(ssh, f"""
mkdir -p /etc/amnezia/amneziawg/
cat > /etc/amnezia/amneziawg/awg0.conf << 'TUNEOF'
[Interface]
PrivateKey = {server_key}
Address = {local_subnet}
ListenPort = {listen_port}
{awg_ini}
TUNEOF
systemctl enable --now awg-quick@awg0 2>/dev/null || awg-quick up awg0
""")
            logger.info("Tunnel endpoint on %s (port=%s, subnet=%s)", foreign.name, listen_port, local_subnet)
            return {
                "server_public_key": server_pub,
                "endpoint": f"{foreign.host}:{listen_port}",
                "local_subnet": local_subnet,
                "awg_params": params,
                "listen_port": listen_port,
            }
        finally:
            ssh.close()

    async def setup_tunnel_client(
        self, russian_server: Server, foreign_endpoint: str,
        foreign_pubkey: str, local_subnet: str, awg_params: dict,
        tunnel_interface: str = "awg1",
    ) -> str:
        """Setup Russian VPS as AWG client to foreign tunnel."""
        import asyncio as aio
        loop = aio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._setup_tunnel_client_sync,
                          russian_server, foreign_endpoint, foreign_pubkey,
                          local_subnet, awg_params, tunnel_interface),
        )

    def _setup_tunnel_client_sync(
        self, russian: Server, foreign_endpoint: str,
        foreign_pubkey: str, local_subnet: str, awg_params: dict,
        tunnel_interface: str,
    ) -> str:
        ssh = self._get_ssh_client(russian)
        client_key = self._exec_command(ssh, "awg genkey").strip()
        client_pub = self._exec_command(ssh, f"echo '{client_key}' | awg pubkey").strip()
        awg_ini = awg2_params_to_ini(awg_params)
        base_ip = local_subnet.rsplit(".", 2)[0]
        client_ip = f"{base_ip}.2/24"

        try:
            self._exec_command(ssh, f"""
cat > /etc/amnezia/amneziawg/{tunnel_interface}.conf << 'TUNCLIENTEOF'
[Interface]
PrivateKey = {client_key}
Address = {client_ip}
MTU = 1280
{awg_ini}

[Peer]
PublicKey = {foreign_pubkey}
Endpoint = {foreign_endpoint}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
TUNCLIENTEOF

systemctl enable --now awg-quick@{tunnel_interface} 2>/dev/null || awg-quick up {tunnel_interface}
""")
            logger.info("Tunnel client %s on %s -> %s", tunnel_interface, russian.name, foreign_endpoint)
            return client_pub
        finally:
            ssh.close()

    async def add_tunnel_peer(
        self, foreign_server: Server, client_pubkey: str,
        client_ip: str = "10.10.0.2/32",
    ) -> str:
        """Add Russian VPS as peer on foreign AWG server."""
        import asyncio as aio
        loop = aio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._add_tunnel_peer_sync,
                          foreign_server, client_pubkey, client_ip),
        )

    def _add_tunnel_peer_sync(self, foreign: Server, client_pubkey: str, client_ip: str) -> str:
        ssh = self._get_ssh_client(foreign)
        try:
            self._exec_command(ssh, f"""
awg set awg0 peer {client_pubkey} allowed-ips {client_ip}
awg syncconf awg0 2>/dev/null || systemctl restart awg-quick@awg0
""")
            logger.info("Peer %s added to tunnel on %s", client_ip, foreign.name)
            return "ok"
        finally:
            ssh.close()

    async def deploy_full_cascade(
        self, russian_server: Server, foreign_server: Server,
        tunnel_interface: str = "awg1", routing_mode: str = "full",
        preset: str = "default",
    ) -> dict:
        """Full cascade: Russia VPS → Foreign VPS → Internet.

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
        foreign_server.api_url = f"http://{foreign_server.host}:7777"

        return {
            "status": "deployed",
            "tunnel_interface": tunnel_interface,
            "endpoint": result["endpoint"],
            "foreign_pubkey": result["server_public_key"],
            "client_pubkey": client_pub,
            "routing_mode": routing_mode,
        }

    async def _setup_full_tunnel_routing(
        self, russian_server: Server, tunnel_interface: str = "awg1",
    ) -> str:
        """Route ALL traffic from awg0 through tunnel to foreign."""
        import asyncio as aio
        loop = aio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self.__setup_full_tunnel_sync, russian_server, tunnel_interface),
        )

    def __setup_full_tunnel_sync(self, russian: Server, tunnel_if: str) -> str:
        ssh = self._get_ssh_client(russian)
        try:
            self._exec_command(ssh, f"""
set -e
grep -q "200 tunnel_out" /etc/iproute2/rt_tables || echo "200 tunnel_out" >> /etc/iproute2/rt_tables
ip route add default dev {tunnel_if} table tunnel_out 2>/dev/null || ip route replace default dev {tunnel_if} table tunnel_out
ip rule add fwmark 0x1 table tunnel_out 2>/dev/null || true
iptables -t mangle -F PREROUTING 2>/dev/null || true
iptables -t mangle -A PREROUTING -i awg0 -j MARK --set-mark 0x1
iptables -t nat -A POSTROUTING -o {tunnel_if} -j MASQUERADE 2>/dev/null || true
iptables -t nat -A POSTROUTING -o awg0 -j MASQUERADE 2>/dev/null || true
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || true
iptables -t nat -A POSTROUTING -o ens3 -j MASQUERADE 2>/dev/null || true
""")
            logger.info("Full tunnel routing on %s via %s", russian.name, tunnel_if)
            return "ok"
        finally:
            ssh.close()
