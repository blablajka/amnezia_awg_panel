"""
GOST (Generic Object Security Tunnel) protocol handler — stub.
"""
from __future__ import annotations

from database.models import Server
from services.protocols.base import BaseProtocolHandler


class GostProtocolHandler(BaseProtocolHandler):
    """Stub for GOST protocol — not implemented yet."""

    async def create_client(self, server: Server, client_name: str) -> tuple[str, str]:
        raise NotImplementedError("GOST protocol handler is not implemented yet")

    async def remove_client(self, server: Server, identifier: str) -> bool:
        raise NotImplementedError("GOST protocol handler is not implemented yet")

    async def get_server_status(self, server: Server) -> dict:
        return {"status": "unknown", "message": "GOST handler not implemented"}

    async def get_client_traffic(self, server: Server, identifier: str) -> tuple[int, int]:
        raise NotImplementedError("GOST protocol handler is not implemented yet")

    async def deploy_server(self, server: Server, **kwargs) -> str:
        raise NotImplementedError("GOST protocol handler is not implemented yet")

    def client_config_format(self) -> str:
        return ".json"
