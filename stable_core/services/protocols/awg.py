"""
AwgProtocolHandler — AmneziaWG via awg-server REST API (primary) + SSH fallback.

CRUD operations use awg-server HTTP API (port 7777).
SSH is reserved for: deploy, syncconf, PSK generation, emergency regen.
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
    """AmneziaWG protocol handler — awg-server HTTP API for daily ops, SSH for admin."""

    def __init__(self, server_manager: ServerManager | None = None):
        self._sm = server_manager or ServerManager()
        self._timeout = 15.0

    def _get_api_url(self, server: Server) -> str:
        if server.api_url:
            return server.api_url.rstrip("/")
        return "http://127.0.0.1:7777"  # awg-server runs locally on VPS

    def _get_headers(self, server: Server) -> dict:
        token = server.api_token or ""
        return {
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
        }

    # ═══════════════════════════════════════════════════════════════
    # BaseProtocolHandler implementation (all via awg-server API)
    # ═══════════════════════════════════════════════════════════════

    async def create_client(self, server: Server, client_name: str) -> tuple[str, str]:
        """Create client via awg-server API. Returns (config_data, client_id)."""
        client_id = str(uuid.uuid4())
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            # 1. Create client
            create_resp = await http.post(
                "%s/api/clients" % api_base,
                headers=headers,
                json={"id": client_id, "name": client_name},
            )
            create_resp.raise_for_status()

            # 2. Get configuration (clean up client on failure)
            try:
                conf_resp = await http.get(
                    "%s/api/clients/%s/configuration" % (api_base, client_id),
                    headers=headers,
                )
                conf_resp.raise_for_status()
                config_data = conf_resp.text
            except Exception:
                # Rollback: remove the orphan client
                try:
                    await http.delete(
                        "%s/api/clients/%s" % (api_base, client_id),
                        headers=headers,
                    )
                except Exception:
                    pass
                raise

            logger.info(
                "AWG client created: name=%s id=%s on %s",
                client_name, client_id, server.name,
            )
            return config_data, client_id

    async def remove_client(self, server: Server, identifier: str) -> bool:
        """Remove client via awg-server API."""
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.delete(
                "%s/api/clients/%s" % (api_base, identifier),
                headers=headers,
            )
            if resp.status_code in (200, 204, 404):
                logger.info("AWG client %s removed from %s", identifier, server.name)
                return True
            resp.raise_for_status()
            return True

    async def get_server_status(self, server: Server) -> dict:
        """Health check via awg-server /health + peer count."""
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                # Health check
                h_resp = await http.get("%s/health" % api_base)
                h_resp.raise_for_status()

                # Client list for peer count
                c_resp = await http.get(
                    "%s/api/clients" % api_base, headers=headers,
                )
                c_resp.raise_for_status()
                clients = c_resp.json()
                peer_count = len(clients) if isinstance(clients, list) else 0

                return {"status": "online", "peers": peer_count}
            except Exception as e:
                logger.warning("Server %s unreachable: %s", server.name, e)
                return {"status": "offline", "error": str(e)}

    async def get_client_traffic(self, server: Server, identifier: str) -> tuple[int, int]:
        """Get per-client traffic via awg-server client list."""
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.get(
                    "%s/api/clients" % api_base, headers=headers,
                )
                resp.raise_for_status()
                clients = resp.json()
                for c in clients if isinstance(clients, list) else []:
                    if c.get("id") == identifier:
                        rx = int(c.get("transferRx", 0) or c.get("transfer_rx", 0))
                        tx = int(c.get("transferTx", 0) or c.get("transfer_tx", 0))
                        return rx, tx
                return 0, 0
            except Exception:
                return 0, 0

    async def deploy_server(self, server: Server, **kwargs) -> str:
        """Deploy AWG 2.0 server via SSH."""
        return await self._sm.deploy_awg_server(server, **kwargs)

    def client_config_format(self) -> str:
        return ".conf"

    # ═══════════════════════════════════════════════════════════════
    # Bulk operations (awg-server API)
    # ═══════════════════════════════════════════════════════════════

    async def list_clients(self, server: Server) -> list[dict]:
        """List all clients with their IDs and traffic stats."""
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.get(
                    "%s/api/clients" % api_base, headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else []
            except Exception:
                return []

    async def get_all_traffic(self, server: Server) -> list[dict]:
        """Get traffic for all clients via awg-server API (preferred) or SSH fallback."""
        api_base = self._get_api_url(server)
        headers = self._get_headers(server)

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.get(
                    "%s/api/clients" % api_base, headers=headers,
                )
                resp.raise_for_status()
                clients = resp.json()
                result = []
                for c in clients if isinstance(clients, list) else []:
                    result.append({
                        "name": c.get("id", c.get("name", "")),
                        "rx": int(c.get("transferRx", 0) or c.get("transfer_rx", 0)),
                        "tx": int(c.get("transferTx", 0) or c.get("transfer_tx", 0)),
                    })
                return result
            except Exception:
                # Fallback to SSH + manage_amneziawg.sh
                return await self._get_all_traffic_ssh(server)

    async def _get_all_traffic_ssh(self, server: Server) -> list[dict]:
        """SSH fallback for traffic stats."""
        import asyncio as aio
        loop = aio.get_event_loop()

        def _sync() -> list[dict]:
            ssh = self._sm._get_ssh_client(server)
            try:
                raw = self._sm._exec_command(
                    ssh,
                    "sudo bash /root/awg/manage_amneziawg.sh stats --json 2>/dev/null || echo '[]'",
                )
                return json.loads(raw) if raw.strip() else []
            except Exception:
                return []
            finally:
                ssh.close()

        return await loop.run_in_executor(None, _sync)

    # ═══════════════════════════════════════════════════════════════
    # Admin operations (SSH-based — not available in awg-server API)
    # ═══════════════════════════════════════════════════════════════

    async def create_client_with_psk(self, server: Server, client_name: str) -> str:
        """Create client with PresharedKey (SSH — for Shadowrocket/iOS)."""
        import asyncio as aio
        loop = aio.get_event_loop()

        def _sync() -> str:
            ssh = self._sm._get_ssh_client(server)
            try:
                self._sm._exec_command(
                    ssh,
                    "sudo bash /root/awg/manage_amneziawg.sh add %s --psk" % client_name,
                )
                conf = self._sm._exec_command(
                    ssh, "cat /root/awg/%s.conf" % client_name,
                )
                return conf
            finally:
                ssh.close()

        return await loop.run_in_executor(None, _sync)

    async def syncconf(self, server: Server) -> str:
        """Hot-reload AWG config (SSH)."""
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

    async def regen_client(self, server: Server, client_name: str) -> str:
        """Regenerate client config from live awg0.conf (SSH)."""
        import asyncio as aio
        loop = aio.get_event_loop()

        def _sync() -> str:
            ssh = self._sm._get_ssh_client(server)
            try:
                self._sm._exec_command(
                    ssh,
                    "sudo bash /root/awg/manage_amneziawg.sh regen %s" % client_name,
                )
                return self._sm._exec_command(
                    ssh, "cat /root/awg/%s.conf" % client_name,
                )
            finally:
                ssh.close()

        return await loop.run_in_executor(None, _sync)
