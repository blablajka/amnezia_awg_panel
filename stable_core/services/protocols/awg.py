"""
AwgProtocolHandler — AmneziaWG via REST API awg-server + SSH for advanced ops.

Uses httpx for CRUD (create/remove/list clients via awg-server REST API).
Falls back to SSH for syncconf hot-reload, PSK, and traffic stats.
"""
from __future__ import annotations

import logging
import uuid
import json

import httpx

from database.models import Server
from services.protocols.base import BaseProtocolHandler
from services.server_manager import ServerManager

logger = logging.getLogger(__name__)


class AwgProtocolHandler(BaseProtocolHandler):
    """Обработчик протокола AmneziaWG (через awg-server API)."""

    def __init__(self, server_manager: ServerManager | None = None):
        self._sm = server_manager or ServerManager()
        self._timeout = 10.0

    def _get_api_url(self, server: Server) -> str:
        """Формирует базовый URL API для сервера."""
        # Если в БД явно указан api_url, используем его, иначе фоллбэк на HTTP-порт по умолчанию
        if server.api_url:
            return server.api_url.rstrip("/")
        return f"http://{server.host}:7777"

    def _get_headers(self, server: Server) -> dict:
        """Формирует заголовки авторизации."""
        token = server.api_token or ""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    # ── BaseProtocolHandler implementation ──────────────────────────

    async def create_client(self, server: Server, client_name: str) -> str:
        """Создать клиента и получить его .conf."""
        # В awg-server клиенты идентифицируются по ID. Мы сгенерируем UUID.
        # Имя клиента (client_name) мы можем сохранить как-то или просто использовать UUID.
        client_id = str(uuid.uuid4())
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # 1. Создаём клиента
                create_resp = await client.post(
                    f"{api_base}/api/clients",
                    headers=headers,
                    json={"id": client_id}
                )
                create_resp.raise_for_status()

                # 2. Получаем его конфигурацию
                conf_resp = await client.get(
                    f"{api_base}/api/clients/{client_id}/configuration",
                    headers=headers
                )
                conf_resp.raise_for_status()
                config_data = conf_resp.text

                logger.info(
                    "AWG клиент %s (id=%s) создан на %s (%s)",
                    client_name, client_id, server.name, server.host
                )
                
                # Мы возвращаем конфиг. Важно: нам нужно где-то сохранить client_id,
                # чтобы потом удалять клиента. В текущей модели UserServer есть 'client_name',
                # мы можем переиспользовать это поле и сохранить в него client_id.
                
                # Если BaseProtocolHandler ожидает возврат конфигурации:
                return config_data, client_id
                
        except Exception as e:
            logger.error("Ошибка создания AWG клиента %s на %s: %s", client_name, server.name, e)
            raise

    async def remove_client(self, server: Server, identifier: str) -> bool:
        """Удалить клиента по его identifier (client_id)."""
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.delete(
                    f"{api_base}/api/clients/{identifier}",
                    headers=headers
                )
                # 404 означает, что клиент уже удалён, что нас тоже устраивает
                if resp.status_code not in (200, 204, 404):
                    resp.raise_for_status()
                
                logger.info("AWG клиент %s удален с %s", identifier, server.name)
                return True
        except Exception as e:
            logger.error("Ошибка удаления AWG клиента %s с %s: %s", identifier, server.name, e)
            return False

    async def get_server_status(self, server: Server) -> dict:
        """Получить статус сервера (health check)."""
        api_base = self._get_api_url(server)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{api_base}/health")
                resp.raise_for_status()
                return {"status": "online", "details": resp.json()}
        except Exception as e:
            logger.warning("Сервер %s недоступен: %s", server.name, e)
            return {"status": "offline", "error": str(e)}

    async def get_client_traffic(self, server: Server, identifier: str) -> tuple[int, int]:
        """Получить трафик клиента (rx_bytes, tx_bytes)."""
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{api_base}/api/clients/{identifier}/stats",
                    headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return int(data.get("rx_bytes", 0)), int(data.get("tx_bytes", 0))
        except Exception as e:
            logger.debug("Не удалось получить трафик для %s на %s: %s", identifier, server.name, e)
            return 0, 0

    async def deploy_server(self, server: Server, **kwargs) -> str:
        """Deploy AWG 2.0 server via SSH."""
        return await self._sm.deploy_awg_server(server, **kwargs)

    def client_config_format(self) -> str:
        return ".conf"

    # ── Advanced: PSK, syncconf, traffic via SSH ────────────────────

    async def create_client_with_psk(self, server: Server, client_name: str) -> str:
        """Create client with PresharedKey via SSH + manage_amneziawg.sh --psk."""
        import asyncio as aio
        loop = aio.get_event_loop()

        def _sync() -> str:
            ssh = self._sm._get_ssh_client(server)
            try:
                self._sm._exec_command(
                    ssh,
                    f"sudo bash /root/awg/manage_amneziawg.sh add {client_name} --psk",
                )
                conf = self._sm._exec_command(ssh, f"cat /root/awg/{client_name}.conf")
                return conf
            finally:
                ssh.close()

        return await loop.run_in_executor(None, _sync)

    async def syncconf(self, server: Server) -> str:
        """Hot-reload AWG config without restart (awg syncconf)."""
        import asyncio as aio
        loop = aio.get_event_loop()

        def _sync() -> str:
            ssh = self._sm._get_ssh_client(server)
            try:
                return self._sm._exec_command(
                    ssh,
                    "awg syncconf awg0 2>/dev/null || systemctl restart awg-quick@awg0",
                )
            finally:
                ssh.close()

        return await loop.run_in_executor(None, _sync)

    async def get_all_traffic(self, server: Server) -> list[dict]:
        """Get traffic for all clients via manage stats --json."""
        import asyncio as aio
        loop = aio.get_event_loop()

        def _sync() -> list[dict]:
            ssh = self._sm._get_ssh_client(server)
            try:
                raw = self._sm._exec_command(
                    ssh,
                    "sudo bash /root/awg/manage_amneziawg.sh stats --json",
                )
                return json.loads(raw)
            except Exception:
                return []
            finally:
                ssh.close()

        return await loop.run_in_executor(None, _sync)

    async def regen_client(self, server: Server, client_name: str) -> str:
        """Regenerate client config from live awg0.conf (source of truth)."""
        import asyncio as aio
        loop = aio.get_event_loop()

        def _sync() -> str:
            ssh = self._sm._get_ssh_client(server)
            try:
                self._sm._exec_command(
                    ssh,
                    f"sudo bash /root/awg/manage_amneziawg.sh regen {client_name}",
                )
                return self._sm._exec_command(ssh, f"cat /root/awg/{client_name}.conf")
            finally:
                ssh.close()

        return await loop.run_in_executor(None, _sync)
