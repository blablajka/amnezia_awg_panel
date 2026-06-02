"""
Server Hardening — настройка безопасности VPS через SSH.
"""
from __future__ import annotations

import asyncio
import logging
from functools import partial

import paramiko

from database.models import Server
from services.server_manager import ServerManager

logger = logging.getLogger(__name__)


class ServerHardening:
    """Автоматическая настройка безопасности сервера."""

    # ── UFW ──────────────────────────────────────────────────────────

    @staticmethod
    async def setup_ufw(server: Server, awg_port: int = 443) -> str:
        """Настроить базовые правила UFW на сервере."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(ServerHardening._setup_ufw_sync, server, awg_port),
        )

    @staticmethod
    def _setup_ufw_sync(server: Server, awg_port: int = 443) -> str:
        ssh = ServerManager._get_ssh_client(server)
        try:
            commands = [
                "ufw default deny incoming",
                "ufw default allow outgoing",
                "ufw allow 22/tcp",
                f"ufw allow {awg_port}/udp",
                "ufw allow 8000/tcp",
                "echo 'y' | ufw enable",
            ]
            results = []
            for cmd in commands:
                output = ServerManager._exec_command(ssh, cmd)
                results.append(f"$ {cmd}\n{output}")
            logger.info("UFW настроен на %s", server.name)
            return "\n".join(results)
        except Exception as e:
            logger.error("Ошибка настройки UFW на %s: %s", server.name, e)
            raise
        finally:
            ssh.close()

    @staticmethod
    async def allow_port(server: Server, port: int, proto: str = "tcp") -> bool:
        """Открыть порт в UFW."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(ServerHardening._allow_port_sync, server, port, proto),
        )

    @staticmethod
    def _allow_port_sync(server: Server, port: int, proto: str = "tcp") -> bool:
        ssh = ServerManager._get_ssh_client(server)
        try:
            ServerManager._exec_command(ssh, f"ufw allow {port}/{proto}")
            logger.info("Порт %s/%s открыт на %s", port, proto, server.name)
            return True
        except Exception as e:
            logger.error("Ошибка открытия порта на %s: %s", server.name, e)
            return False
        finally:
            ssh.close()

    # ── fail2ban ──────────────────────────────────────────────────────

    @staticmethod
    async def setup_fail2ban(server: Server) -> str:
        """Установить и настроить fail2ban для защиты SSH."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(ServerHardening._setup_fail2ban_sync, server),
        )

    @staticmethod
    def _setup_fail2ban_sync(server: Server) -> str:
        ssh = ServerManager._get_ssh_client(server)
        try:
            commands = [
                "apt-get update -qq && apt-get install -y -qq fail2ban",
                "systemctl enable fail2ban",
                "systemctl start fail2ban",
            ]
            results = []
            for cmd in commands:
                output = ServerManager._exec_command(ssh, cmd)
                results.append(f"$ {cmd}\n{output[:200]}")
            logger.info("fail2ban настроен на %s", server.name)
            return "\n".join(results)
        except Exception as e:
            logger.error("Ошибка настройки fail2ban на %s: %s", server.name, e)
            raise
        finally:
            ssh.close()

    # ── Полный hardening ─────────────────────────────────────────────

    @staticmethod
    async def full_hardening(server: Server, awg_port: int = 443) -> dict:
        """Выполнить полный hardening сервера (UFW + fail2ban)."""
        result = {"ufw": None, "fail2ban": None}

        try:
            result["ufw"] = await ServerHardening.setup_ufw(server, awg_port)
        except Exception as e:
            result["ufw"] = f"ERROR: {e}"

        try:
            result["fail2ban"] = await ServerHardening.setup_fail2ban(server)
        except Exception as e:
            result["fail2ban"] = f"ERROR: {e}"

        return result
