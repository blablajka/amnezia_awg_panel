"""
Абстрактный базовый класс для VPN-протоколов.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from database.models import Server


class BaseProtocolHandler(ABC):
    """Общий интерфейс для всех VPN-протокольных хендлеров."""

    @abstractmethod
    async def create_client(self, server: Server, client_name: str) -> tuple[str, str]:
        """Создать клиента, вернуть (config_data, client_identifier)."""
        ...

    @abstractmethod
    async def remove_client(self, server: Server, public_key: str) -> bool:
        """Удалить клиента по идентификатору (ключ / имя / UUID)."""
        ...

    @abstractmethod
    async def get_server_status(self, server: Server) -> dict:
        """Статус сервера и количество подключённых клиентов."""
        ...

    @abstractmethod
    async def get_client_traffic(self, server: Server, identifier: str) -> tuple[int, int]:
        """Трафик клиента: (rx_bytes, tx_bytes)."""
        ...

    @abstractmethod
    async def deploy_server(self, server: Server, **kwargs) -> str:
        """Развернуть серверный контейнер, вернуть статус."""
        ...

    @abstractmethod
    def client_config_format(self) -> str:
        """Расширение файла клиентского конфига ('.conf', '.yaml', '.json')."""
        ...
