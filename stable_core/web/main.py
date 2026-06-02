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
import sys
import asyncio
from pathlib import Path

# Настройка логирования
logger = logging.getLogger("panel")
logger.setLevel(logging.INFO)

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
    # Root URL redirects to admin login
    @app.get("/")
    async def root_redirect():
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    # ── Фоновые задачи ───────────────────────────────────────────────

    async def cleanup_loop():
        from services.subscription_service import SubscriptionService
        while True:
            try:
                async with async_session_factory() as session:
                    await SubscriptionService.deactivate_expired(session)
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")
            await asyncio.sleep(86400)

    async def traffic_billing_loop():
        """Sync traffic stats from servers to DB every 5 minutes."""
        from database import crud
        from services.protocols.awg import AwgProtocolHandler
        awg = AwgProtocolHandler()
        while True:
            try:
                async with async_session_factory() as session:
                    servers = await crud.get_active_servers(session)
                    for server in servers:
                        try:
                            all_traffic = await awg.get_all_traffic(server)
                            for entry in all_traffic:
                                # Update UserServer traffic counters
                                name = entry.get("name", "")
                                rx = int(entry.get("rx", 0))
                                tx = int(entry.get("tx", 0))
                                if name and (rx > 0 or tx > 0):
                                    from sqlalchemy import update
                                    from database.models import UserServer
                                    await session.execute(
                                        update(UserServer)
                                        .where(UserServer.client_name == name)
                                        .values(traffic_rx=UserServer.traffic_rx + rx,
                                                traffic_tx=UserServer.traffic_tx + tx)
                                    )
                            await session.commit()
                        except Exception:
                            pass  # Server offline or no stats
            except Exception as e:
                logger.error(f"Traffic billing error: {e}")
            await asyncio.sleep(300)  # Every 5 minutes

    @app.on_event("startup")
    async def startup_event():
        from database.session import init_db
        await init_db()
        asyncio.create_task(cleanup_loop())
        asyncio.create_task(traffic_billing_loop())

        # Start Telegram bot in background (non-blocking)
        if settings.BOT_TOKEN and settings.BOT_TOKEN != "dummy_token_to_allow_startup":
            try:
                from bot.handlers import start_bot
                asyncio.create_task(start_bot())
                logger.info("Telegram bot background task created")
            except Exception as e:
                logger.warning("Telegram bot failed to start: %s", e)

    # ── Login / Logout ───────────────────────────────────────────────

    @admin_router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        token = get_session_token(request)
        if verify_session(token):
            return RedirectResponse(f"{settings.ADMIN_PATH}/dashboard", status_code=302)
        return templates.TemplateResponse(request=request, name="login.html", context={
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

        return templates.TemplateResponse(request=request, name="login.html", context={
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


if __name__ == "__main__":
    import uvicorn
    app = create_web_app()
    uvicorn.run(app, host=settings.WEB_HOST, port=settings.WEB_PORT)
