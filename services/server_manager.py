"""
Server Manager — управление VPN серверами через SSH.

Устанавливает awg-server, настраивает сплит-роутинг и мосты,
а также собирает системные метрики (CPU, RAM, Disk).
"""
from __future__ import annotations

import asyncio
import logging
from functools import partial
import secrets

import paramiko

from database.models import Server

logger = logging.getLogger(__name__)


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
            # Здесь надо бы сохранить token в БД, но это сделает вызывающий код

        try:
            # Установка AmneziaWG + awg-server
            install_script = f"""
            set -e
            export DEBIAN_FRONTEND=noninteractive
            apt update && apt install -y build-essential git dkms linux-headers-$(uname -r) curl ipset iptables iproute2

            # 1. Install AmneziaWG 2.0 kernel module
            if ! lsmod | grep -q amneziawg; then
                rm -rf /tmp/amneziawg-module
                git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-linux-kernel-module.git /tmp/amneziawg-module
                cd /tmp/amneziawg-module/src
                make dkms-install || true
                dkms add -m amneziawg -v 1.0.0 || true
                dkms build -m amneziawg -v 1.0.0 || true
                dkms install -m amneziawg -v 1.0.0 || true
                modprobe amneziawg
            fi

            # 2. Install awg CLI
            if ! command -v awg > /dev/null; then
                rm -rf /tmp/amneziawg-tools
                git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-tools.git /tmp/amneziawg-tools
                make -C /tmp/amneziawg-tools/src && make -C /tmp/amneziawg-tools/src install
            fi

            # 3. Enable IP forwarding
            sysctl -w net.ipv4.ip_forward=1
            echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-ipforward.conf

            # 4. Download awg-server
            curl -fsSL https://github.com/stealthsurf-vpn/awg-server/releases/latest/download/awg-server-linux-$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/') -o /usr/local/bin/awg-server
            chmod +x /usr/local/bin/awg-server

            # 5. Create data dir
            mkdir -p /data

            # 6. Create systemd service
            cat > /etc/systemd/system/awg-server.service <<EOF
[Unit]
Description=AmneziaWG Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/awg-server
Restart=always
RestartSec=5

Environment=AWG_API_TOKEN={token}
Environment=AWG_ADDRESS=10.0.0.1/24
Environment=AWG_ENDPOINT={server.host}
Environment=AWG_JC=5
Environment=AWG_JMIN=50
Environment=AWG_JMAX=1000

[Install]
WantedBy=multi-user.target
EOF

            # 7. Start service
            systemctl daemon-reload
            systemctl enable --now awg-server
            """
            output = self._exec_command(ssh, install_script)
            logger.info("AWG-Server установлен на %s. Token: %s", server.name, token)
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
