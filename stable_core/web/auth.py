"""
Web Authentication — file-backed session auth for admin panel.
Sessions survive server restarts via JSON file in /data/sessions.json.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse

from config import settings

# File-backed session store (survives restarts, thread-safe)
SESSIONS_FILE = Path("/data/sessions.json")
_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = asyncio.Lock()

# Max session age: 7 days
SESSION_MAX_AGE = 86400 * 7


def _load_sessions() -> None:
    """Load sessions from disk on startup."""
    global _sessions
    try:
        if SESSIONS_FILE.exists():
            raw = json.loads(SESSIONS_FILE.read_text())
            now = time.time()
            # Drop expired sessions
            _sessions = {
                k: v for k, v in raw.items()
                if v.get("_created", 0) + SESSION_MAX_AGE > now
            }
    except Exception:
        _sessions = {}


def _save_sessions() -> None:
    """Persist sessions to disk atomically."""
    try:
        SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SESSIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_sessions))
        tmp.rename(SESSIONS_FILE)
    except Exception:
        pass  # Non-critical — sessions survive in memory


# Load on import
_load_sessions()


async def create_session(username: str) -> str:
    """Create new session, return token."""
    token = secrets.token_urlsafe(32)
    async with _sessions_lock:
        _sessions[token] = {"username": username, "_created": time.time()}
        _save_sessions()
    return token


async def verify_session(token: str | None) -> bool:
    """Check if session is valid."""
    if not token:
        return False
    async with _sessions_lock:
        session = _sessions.get(token)
        if not session:
            return False
        if session.get("_created", 0) + SESSION_MAX_AGE < time.time():
            _sessions.pop(token, None)
            _save_sessions()
            return False
        return True


async def delete_session(token: str) -> None:
    """Remove session."""
    async with _sessions_lock:
        _sessions.pop(token, None)
        _save_sessions()


def check_credentials(username: str, password: str) -> bool:
    """Verify login/password against settings."""
    return (
        username == settings.ADMIN_USERNAME
        and password == settings.ADMIN_PASSWORD
    )


def get_session_token(request: Request) -> str | None:
    """Extract session token from cookies."""
    return request.cookies.get("session_token")


async def require_auth(request: Request) -> bool:
    """Check authorization. Used in dependencies."""
    token = get_session_token(request)
    return await verify_session(token)
