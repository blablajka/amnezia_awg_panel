"""
Hysteria2 protocol handler — stub.
"""
from __future__ import annotations

from database.models import Server
from services.protocols.base import BaseProtocolHandler


class Hysteria2ProtocolHandler(BaseProtocolHandler):
    """Stub for Hysteria2 protocol — not implemented yet."""

    async def create_client(self, server: Server, client_name: str) -> tuple[str, str]:
        raise NotImplementedError("Hysteria2 protocol handler is not implemented yet")

    async def remove_client(self, server: Server, identifier: str) -> bool:
        raise NotImplementedError("Hysteria2 protocol handler is not implemented yet")

    async def get_server_status(self, server: Server) -> dict:
        return {"status": "unknown", "message": "Hysteria2 handler not implemented"}

    async def get_client_traffic(self, server: Server, identifier: str) -> tuple[int, int]:
        raise NotImplementedError("Hysteria2 protocol handler is not implemented yet")

    async def deploy_server(self, server: Server, **kwargs) -> str:
        raise NotImplementedError("Hysteria2 protocol handler is not implemented yet")

    def client_config_format(self) -> str:
        return ".yaml"
