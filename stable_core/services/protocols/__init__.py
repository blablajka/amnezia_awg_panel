"""
Protocol handlers for different VPN protocols.

Each protocol (AWG) implements BaseProtocolHandler.
ServerManager delegates protocol-specific operations to these handlers.
"""

from services.protocols.base import BaseProtocolHandler
from services.protocols.awg import AwgProtocolHandler


def get_protocol_handler(name: str) -> BaseProtocolHandler:
    """Получить хендлер протокола по имени."""
    handlers = {
        "awg": AwgProtocolHandler,
    }
    handler_class = handlers.get(name.lower())
    if not handler_class:
        raise ValueError(f"Неизвестный протокол: {name}. Доступны: {list(handlers.keys())}")
    return handler_class()


__all__ = [
    "BaseProtocolHandler",
    "AwgProtocolHandler",
    "get_protocol_handler",
]
