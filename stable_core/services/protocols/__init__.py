"""
Protocol handlers for different VPN protocols.

Each protocol (AWG, GOST, Hysteria2) implements BaseProtocolHandler.
ServerManager delegates protocol-specific operations to these handlers.
"""

from services.protocols.base import BaseProtocolHandler
from services.protocols.awg import AwgProtocolHandler
from services.protocols.gost import GostProtocolHandler
from services.protocols.hysteria2 import Hysteria2ProtocolHandler


def get_protocol_handler(name: str) -> BaseProtocolHandler:
    """Get protocol handler by name."""
    handlers = {
        "awg": AwgProtocolHandler,
        "gost": GostProtocolHandler,
        "hysteria2": Hysteria2ProtocolHandler,
    }
    handler_class = handlers.get(name.lower())
    if not handler_class:
        raise ValueError(f"Unknown protocol: {name}. Available: {list(handlers.keys())}")
    return handler_class()


__all__ = [
    "BaseProtocolHandler",
    "AwgProtocolHandler",
    "GostProtocolHandler",
    "Hysteria2ProtocolHandler",
    "get_protocol_handler",
]
