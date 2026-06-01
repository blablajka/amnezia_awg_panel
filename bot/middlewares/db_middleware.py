"""
Middleware для инъекции AsyncSession в каждый handler.

Автоматически создаёт сессию БД, передаёт в handler через data["session"],
коммитит при успехе, откатывает при ошибке.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from database.session import async_session_factory


class DatabaseMiddleware(BaseMiddleware):
    """Middleware для автоматического управления сессией БД."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
