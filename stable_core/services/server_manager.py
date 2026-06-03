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
        """Execute command via SSH, return stdout. Raises RuntimeError on non-zero exit."""
        logger.debug("SSH Exec: %s", command[:200])
        _, stdout, stderr = ssh.exec_command(command, timeout=120)
        output = stdout.read().decode("utf-8").strip()
        errors = stderr.read().decode("utf-8").strip()
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RuntimeError(
                "Command failed (exit=%d): %s\nstderr: %s" % (exit_code, command[:200], errors[:500]),
            )
        if errors and "WARNING" not in errors:
            logger.warning("SSH stderr: %s", errors[:200])
        return output

    # ── Server Provisioning ──────────────────────────────────────────

    async def deploy_awg_server(self, server: Server, **kwargs) -> str:
        """Развернуть awg-server на Linux."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._deploy_awg_server_sync, server, kwargs),
        )

    @staticmethod
    def _sftp_put_text(ssh: paramiko.SSHClient, remote_path: str, content: str):
        """Upload text content to remote file via SFTP."""
        sftp = ssh.open_sftp()
        try:
            with sftp.file(remote_path, "w") as f:
                f.write(content)
        finally:
            sftp.close()

    def _build_awg_server_conf(self, server: Server, awg_params: dict) -> str:
        """Build awg0.conf server config from AWG 2.0 params."""
        lines = ["[Interface]"]
        lines.append("Address = 10.0.0.1/24")
        lines.append("ListenPort = %s" % (server.awg_listen_port or 39743))
        # AWG 2.0 obfuscation params
        for key in ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4"):
            if key in awg_params:
                lines.append("%s = %s" % (key, awg_params[key]))
        if "I1" in awg_params:
            lines.append("I1 = %s" % awg_params["I1"])

        if server.ipv6_enabled:
            ipv6_sub = server.ipv6_subnet or DEFAULT_IPV6_SUBNET
            ipv6_addr = ipv6_sub.rstrip("/64").rstrip(":") + "::1/64"
            lines.append("Address = %s" % ipv6_addr)

        lines.append("")
        lines.append("PostUp = iptables -I FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE")
        lines.append("PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE")
        if server.ipv6_enabled:
            lines.append("PostUp = ip6tables -I FORWARD -i %i -j ACCEPT; ip6tables -t nat -A POSTROUTING -o eth0 -j MASQUERADE")
            lines.append("PostDown = ip6tables -D FORWARD -i %i -j ACCEPT; ip6tables -t nat -D POSTROUTING -o eth0 -j MASQUERADE")
        lines.append("")
        return "\n".join(lines)

    def _build_awg_server_systemd(self, server: Server, awg_params: dict, token: str) -> str:
        """Build awg-server systemd unit with full AWG 2.0 environment."""
        listen_port = str(server.awg_listen_port or 39743)
        lines = [
            "[Unit]",
            "Description=AmneziaWG REST API Server",
            "After=awg-quick@awg0.service",
            "Requires=awg-quick@awg0.service",
            "",
            "[Service]",
            "Type=simple",
            "ExecStart=/usr/local/bin/awg-server",
            "Restart=always",
            "RestartSec=5",
            "Environment=AWG_API_TOKEN=%s" % token,
            "Environment=AWG_ADDRESS=10.0.0.1/24",
            "Environment=AWG_ENDPOINT=%s" % server.host,
            "Environment=AWG_LISTEN_PORT=%s" % listen_port,
            "Environment=AWG_HTTP_PORT=7777",
            "Environment=AWG_DATA_DIR=/data",
        ]
        for key in ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4", "I1"):
            val = awg_params.get(key, "")
            lines.append("Environment=AWG_%s=%s" % (key.upper(), val))
        lines.extend([
            "",
            "[Install]",
            "WantedBy=multi-user.target",
        ])
        return "\n".join(lines) + "\n"

    def _deploy_awg_server_sync(self, server: Server, kwargs: dict) -> str:
        """Deploy AmneziaWG 2.0 + awg-server + security on pristine Debian/Ubuntu VPS.

        Steps:
        1. bivlked installer (kernel module + AWG 2.0 tools)
        2. IP forwarding (v4 + optional v6)
        3. awg0.conf generation + upload
        4. Hot-reload AWG config
        5. awg-server binary + systemd
        6. AS Network List (block scanners via ipset)
        7. Zapret (DPI bypass)
        8. node_exporter (Prometheus system metrics)
        """
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
        preset_val = server.awg_preset or "default"
        listen_port = server.awg_listen_port or 39743

        logs: list[str] = []

        try:
            # ── Step 1: bivlked installer ──────────────────────────
            if not kwargs.get("skip_installer"):
                logger.info("[%s] Step 1/8: Installing AWG 2.0 via installer", server.name)
                installer_cmd = (
                    "set -e; export DEBIAN_FRONTEND=noninteractive\n"
                    "apt-get update -qq && apt-get install -y -qq curl ipset iptables iproute2\n"
                    "if ! lsmod | grep -q amneziawg; then\n"
                    "  cd /tmp\n"
                    "  curl -fsSL https://raw.githubusercontent.com/bivlked/amneziawg-installer/v5.15.2/install_amneziawg.sh -o install_awg.sh\n"
                    "  bash install_awg.sh --preset=%s --port=%s --yes --route-amnezia --no-tweaks\n"
                    "fi\n"
                ) % (preset_val, listen_port)
                out = self._exec_command(ssh, installer_cmd)
                logs.append("installer: ok")

            # ── Step 2: IP forwarding ──────────────────────────────
            logger.info("[%s] Step 2/8: Enabling IP forwarding", server.name)
            fwd_script = (
                "sysctl -w net.ipv4.ip_forward=1\n"
                "echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-ipforward.conf\n"
            )
            if server.ipv6_enabled:
                fwd_script += (
                    "sysctl -w net.ipv6.conf.all.forwarding=1\n"
                    "echo 'net.ipv6.conf.all.forwarding=1' >> /etc/sysctl.d/99-ipforward.conf\n"
                )
            self._exec_command(ssh, fwd_script)
            logs.append("ip_forward: ok")

            # ── Step 3: Generate and upload awg0.conf ──────────────
            logger.info("[%s] Step 3/8: Uploading awg0.conf", server.name)
            server_conf = self._build_awg_server_conf(server, awg_params)
            # Fetch or generate server private key
            key_cmd = "cat /root/awg/server_private.key 2>/dev/null || awg genkey"
            server_key = self._exec_command(ssh, key_cmd).strip()
            if not server_key:
                raise RuntimeError("Failed to get/generate server private key")
            # Save key if just generated
            self._exec_command(
                ssh,
                "mkdir -p /root/awg && echo '%s' > /root/awg/server_private.key" % server_key,
            )
            # Build final config with key
            final_conf = "PrivateKey = %s\n" % server_key + server_conf
            self._sftp_put_text(ssh, "/etc/amnezia/amneziawg/awg0.conf", final_conf)
            logs.append("awg0.conf: uploaded")

            # ── Step 4: Hot-reload AWG ─────────────────────────────
            logger.info("[%s] Step 4/8: Hot-reloading AWG config", server.name)
            out = self._exec_command(
                ssh,
                "awg syncconf awg0 2>/dev/null || systemctl restart awg-quick@awg0",
            )
            logs.append("syncconf: %s" % (out[:80] if out else "ok"))

            # ── Step 5: Install awg-server binary ──────────────────
            logger.info("[%s] Step 5/8: Installing awg-server binary", server.name)
            arch_detect = 'uname -m | sed "s/x86_64/amd64/" | sed "s/aarch64/arm64/"'
            awg_server_url = (
                "https://github.com/stealthsurf-vpn/awg-server/releases/latest/download/"
                "awg-server-linux-$(%s)" % arch_detect
            )
            out = self._exec_command(ssh, (
                "curl -fsSL '%s' -o /usr/local/bin/awg-server\n"
                "chmod +x /usr/local/bin/awg-server\n"
                "echo 'installed'"
            ) % awg_server_url)
            logs.append("awg-server: %s" % out.strip())

            # ── Step 6: Systemd unit + start ───────────────────────
            logger.info("[%s] Step 6/8: Starting awg-server service", server.name)
            unit_content = self._build_awg_server_systemd(server, awg_params, token)
            self._sftp_put_text(ssh, "/etc/systemd/system/awg-server.service", unit_content)
            out = self._exec_command(ssh, (
                "systemctl daemon-reload\n"
                "systemctl enable --now awg-server\n"
                "sleep 2\n"
                "systemctl is-active awg-server && echo 'ACTIVE' || echo 'FAILED'"
            ))
            logs.append("service: %s" % out.strip())

            # ── Step 7: AS Network List (block scanners) ─────────
            logger.info("[%s] Step 7/8: Installing AS Network List", server.name)
            try:
                self._exec_command(ssh, (
                    "wget -qO- https://raw.githubusercontent.com/blablajka/AS_Network_List_for-debian/main/install.sh | bash"
                ))
                logs.append("as_list: ok")
            except Exception as _e:
                logger.warning("[%s] AS list install optional: %s", server.name, _e)
                logs.append("as_list: skipped (%s)" % str(_e)[:50])

            # ── Step 8: Zapret (DPI bypass) ─────────────────────
            logger.info("[%s] Step 8/8: Installing Zapret", server.name)
            try:
                self._exec_command(ssh, (
                    "set -e\n"
                    "if [ ! -d /opt/zapret ]; then\n"
                    "  cd /tmp\n"
                    "  git clone --depth=1 https://github.com/bol-van/zapret.git\n"
                    "  cd zapret\n"
                    "  echo 5 | ./install_easy.sh\n"
                    "fi\n"
                    "echo 'zapret_ok'"
                ))
                logs.append("zapret: ok")
            except Exception as _e:
                logger.warning("[%s] Zapret install optional: %s", server.name, _e)
                logs.append("zapret: skipped (%s)" % str(_e)[:50])

            # ── Step 9: node_exporter (Prometheus metrics) ───────
            logger.info("[%s] Installing node_exporter", server.name)
            try:
                self._exec_command(ssh, (
                    "set -e\n"
                    "if ! command -v node_exporter >/dev/null 2>&1; then\n"
                    "  NODE_VERSION=1.8.2\n"
                    "  ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')\n"
                    "  cd /tmp\n"
                    "  curl -fsSL \"https://github.com/prometheus/node_exporter/releases/download/v${NODE_VERSION}/node_exporter-${NODE_VERSION}.linux-${ARCH}.tar.gz\" -o node_exporter.tgz\n"
                    "  tar xzf node_exporter.tgz\n"
                    "  mv node_exporter-*.linux-*/node_exporter /usr/local/bin/\n"
                    "  rm -rf node_exporter*\n"
                    "  cat > /etc/systemd/system/node_exporter.service << 'UNIT'\n"
                    "[Unit]\nDescription=Prometheus Node Exporter\nAfter=network.target\n\n"
                    "[Service]\nType=simple\nExecStart=/usr/local/bin/node_exporter --collector.systemd\nRestart=always\n\n"
                    "[Install]\nWantedBy=multi-user.target\nUNIT\n"
                    "  systemctl daemon-reload\n"
                    "  systemctl enable --now node_exporter\n"
                    "fi\n"
                    "echo 'node_exporter_ok'"
                ))
                logs.append("node_exporter: ok")
            except Exception as _e:
                logger.warning("[%s] node_exporter optional: %s", server.name, _e)
                logs.append("node_exporter: skipped")

            # Save API URL for future use
            if not server.api_url:
                server.api_url = "http://127.0.0.1:7777"

            logger.info(
                "[%s] AWG 2.0 deployed: preset=%s port=%s | %s",
                server.name, server.awg_preset, listen_port, " | ".join(logs),
            )
            return "\n".join(logs)

        except Exception as e:
            logger.error("[%s] Deploy failed: %s", server.name, e)
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
        """Split routing: RU sites + YouTube → direct, everything else → tunnel.

        Uses two ipsets: ru_ips (all Russian IPv4) and direct_ips (RU + YouTube).
        Traffic matching direct_ips goes out directly (eth0).
        Everything else is marked 0x1 and routed through the tunnel interface.
        """
        ssh = self._get_ssh_client(server)
        try:
            script = """#!/bin/bash
            set -e
            apt-get install -y -qq ipset iptables iproute2 curl
            CLIENT_IF="awg0"

            # ── ipset: ru_ips (all Russian subnets) ──────────────────
            ipset create ru_ips hash:net -exist
            ipset flush ru_ips
            curl -sL https://raw.githubusercontent.com/herrbischoff/country-ip-blocks/master/ipv4/ru.cidr > /tmp/ru.cidr
            sed -e "s/^/add ru_ips /" /tmp/ru.cidr > /tmp/ru_restore.txt
            ipset restore -exist < /tmp/ru_restore.txt

            # ── ipset: youtube_ips (Google/YouTube CDN) ──────────────
            ipset create youtube_ips hash:net -exist
            ipset flush youtube_ips
            # Static YouTube CDN ranges (updated manually)
            for cidr in 173.194.0.0/16 74.125.0.0/16 216.58.192.0/19 142.250.0.0/15 142.251.0.0/16 172.217.0.0/16 64.233.160.0/19 66.249.80.0/20 72.14.192.0/18 209.85.128.0/17 66.102.0.0/20 35.190.0.0/17; do
                ipset add youtube_ips $cidr -exist
            done

            # ── ipset: direct_ips = ru_ips ∪ youtube_ips ────────────
            ipset create direct_ips list:set -exist
            ipset flush direct_ips
            ipset add direct_ips ru_ips -exist
            ipset add direct_ips youtube_ips -exist

            # ── Routing table for tunnel ────────────────────────────
            grep -q "100 vpn_bridge" /etc/iproute2/rt_tables || echo "100 vpn_bridge" >> /etc/iproute2/rt_tables
            ip route replace default dev {tunnel_if} table vpn_bridge 2>/dev/null || ip route add default dev {tunnel_if} table vpn_bridge
            ip rule add fwmark 0x1 table vpn_bridge 2>/dev/null || true

            # ── Mangle: direct_ips → RETURN (go direct), rest → MARK 0x1 (tunnel) ──
            iptables -t mangle -F PREROUTING 2>/dev/null || true
            iptables -t mangle -A PREROUTING -i $CLIENT_IF -m set --match-set direct_ips dst -j RETURN
            iptables -t mangle -A PREROUTING -i $CLIENT_IF -j MARK --set-mark 0x1

            # ── NAT MASQUERADE on all outbound interfaces ───────────
            for iface in eth0 ens3 $CLIENT_IF {tunnel_if}; do
                iptables -t nat -C POSTROUTING -o $iface -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o $iface -j MASQUERADE
            done

            echo "Split routing: RU+YouTube=direct, rest=tunnel({tunnel_if})"
            """.format(tunnel_if=tunnel_interface)
            output = self._exec_command(ssh, script)
            logger.info("[%s] Split routing configured: RU+YouTube direct, foreign via %s", server.name, tunnel_interface)
            return output
        except Exception as e:
            logger.error("[%s] Split routing failed: %s", server.name, e)
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
        ssh = None
        try:
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
            if ssh:
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
        ssh = None
        try:
            ssh = self._get_ssh_client(russian)
            client_key = self._exec_command(ssh, "awg genkey").strip()
            client_pub = self._exec_command(ssh, "echo '%s' | awg pubkey" % client_key).strip()
            awg_ini = awg2_params_to_ini(awg_params)
            base_ip = local_subnet.rsplit(".", 2)[0]
            client_ip = "%s.2/24" % base_ip
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
            if ssh:
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
