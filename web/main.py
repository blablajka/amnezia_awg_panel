"""
FastAPI Web Application — админ-панель + YooKassa webhook.

Включает:
  - Jinja2 шаблоны с Tailwind CSS
  - Аутентификация (login/logout)
  - Dashboard с метриками
  - YooKassa webhook endpoint
"""
from __future__ import annotations

import json
import logging
from loguru import logger
import sys
from pathlib import Path

# Настройка логирования
logger.remove()
logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
logger.add("logs/panel.log", rotation="10 MB", retention="7 days", level="INFO")

from fastapi import FastAPI, Request, Form, Depends, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import settings
from database.session import async_session_factory
from web.auth import (
    check_credentials, create_session,
    delete_session, get_session_token, require_auth, verify_session,
)
from web.routers import dashboard, users, subscriptions, servers, promo_codes, stats, protocols, bridges

logger = logging.getLogger(__name__)

# ── Пути ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def create_web_app() -> FastAPI:
    """Создать и настроить FastAPI приложение."""
    app = FastAPI(
        title="Amnezia VPN Admin",
        docs_url=None,
        redoc_url=None,
    )

    # Статические файлы
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Шаблоны
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates

    # ── Роутеры (под секретным префиксом) ────────────────────────────
    admin_router = APIRouter(prefix=settings.ADMIN_PATH)
    admin_router.include_router(dashboard.router)
    admin_router.include_router(users.router)
    admin_router.include_router(subscriptions.router)
    admin_router.include_router(servers.router)
    admin_router.include_router(promo_codes.router)
    admin_router.include_router(stats.router)
    admin_router.include_router(protocols.router)
    admin_router.include_router(bridges.router)
    # Заглушка для корневого URL (защита от сканеров)
    @app.get("/")
    async def root_stub():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not Found")

    # ── Фоновые задачи ───────────────────────────────────────────────
    import asyncio
    
    async def cleanup_loop():
        from services.subscription_service import SubscriptionService
        while True:
            try:
                async with async_session_factory() as session:
                    await SubscriptionService.deactivate_expired(session)
            except Exception as e:
                logger.error(f"Ошибка в cleanup_loop: {e}")
            await asyncio.sleep(86400)  # Раз в день (24 часа)

    @app.on_event("startup")
    async def startup_event():
        from database.session import init_db
        await init_db()
        asyncio.create_task(cleanup_loop())

    # ── Login / Logout ───────────────────────────────────────────────
    from fastapi import Form
    from web.auth import get_session_token, verify_session, check_credentials, create_session, delete_session

    @admin_router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        token = get_session_token(request)
        if verify_session(token):
            return RedirectResponse(f"{settings.ADMIN_PATH}/dashboard", status_code=302)
        return templates.TemplateResponse("login.html", {
            "request": request, "error": None, "admin_path": settings.ADMIN_PATH
        })

    @admin_router.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        if check_credentials(username, password):
            token = create_session(username)
            response = RedirectResponse(f"{settings.ADMIN_PATH}/dashboard", status_code=302)
            response.set_cookie(
                "session_token", token,
                httponly=True,
                max_age=86400 * 7,  # 7 дней
            )
            return response

        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверный логин или пароль",
            "admin_path": settings.ADMIN_PATH
        })

    @admin_router.get("/logout")
    async def logout(request: Request):
        token = get_session_token(request)
        if token:
            delete_session(token)
        response = RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
        response.delete_cookie("session_token")
        return response

    app.include_router(admin_router)
    return app
