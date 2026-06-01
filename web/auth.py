"""
Web Authentication — простая сессионная аутентификация для админ-панели.
"""
from __future__ import annotations

import hashlib
import secrets
from functools import wraps
from typing import Any

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings

# Простое хранилище сессий (в продакшне — Redis)
_sessions: dict[str, dict[str, Any]] = {}


def create_session(username: str) -> str:
    """Создать новую сессию и вернуть token."""
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"username": username}
    return token


def verify_session(token: str | None) -> bool:
    """Проверить, валидна ли сессия."""
    if not token:
        return False
    return token in _sessions


def delete_session(token: str) -> None:
    """Удалить сессию."""
    _sessions.pop(token, None)


def check_credentials(username: str, password: str) -> bool:
    """Проверить логин/пароль."""
    return (
        username == settings.ADMIN_USERNAME
        and password == settings.ADMIN_PASSWORD
    )


def get_session_token(request: Request) -> str | None:
    """Извлечь токен сессии из cookies."""
    return request.cookies.get("session_token")


def require_auth(request: Request) -> bool:
    """Проверить авторизацию. Используется в dependencies."""
    token = get_session_token(request)
    return verify_session(token)
