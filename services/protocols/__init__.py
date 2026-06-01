"""
Protocol handlers for different VPN protocols.

Each protocol (AWG, Hysteria2, GOST) implements BaseProtocolHandler.
ServerManager delegates protocol-specific operations to these handlers.
"""

from services.protocols.base import BaseProtocolHandler
from services.protocols.awg import AwgProtocolHandler
from services.protocols.hysteria2 import Hysteria2ProtocolHandler
from services.protocols.gost import GostProtocolHandler


def get_protocol_handler(name: str) -> BaseProtocolHandler:
    """Получить хендлер протокола по имени."""
    handlers = {
        "awg": AwgProtocolHandler,
        "hysteria2": Hysteria2ProtocolHandler,
        "gost": GostProtocolHandler,
    }
    handler_class = handlers.get(name.lower())
    if not handler_class:
        raise ValueError(f"Неизвестный протокол: {name}. Доступны: {list(handlers)}")
    return handler_class()


__all__ = [
    "BaseProtocolHandler",
    "AwgProtocolHandler",
    "Hysteria2ProtocolHandler",
    "GostProtocolHandler",
    "get_protocol_handler",
]
