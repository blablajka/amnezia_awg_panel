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
from pathlib import Path

from fastapi import FastAPI, Request, Form, Depends
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

    # ── Роутеры ──────────────────────────────────────────────────────
    app.include_router(dashboard.router)
    app.include_router(users.router)
    app.include_router(subscriptions.router)
    app.include_router(servers.router)
    app.include_router(promo_codes.router)
    app.include_router(stats.router)
    app.include_router(protocols.router)
    app.include_router(bridges.router)

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
        asyncio.create_task(cleanup_loop())

    # ── Login / Logout ───────────────────────────────────────────────

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        token = get_session_token(request)
        if verify_session(token):
            return RedirectResponse("/dashboard", status_code=302)
        return templates.TemplateResponse("login.html", {
            "request": request, "error": None,
        })

    @app.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        if check_credentials(username, password):
            token = create_session(username)
            response = RedirectResponse("/dashboard", status_code=302)
            response.set_cookie(
                "session_token", token,
                httponly=True,
                max_age=86400 * 7,  # 7 дней
            )
            return response

        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверный логин или пароль",
        })

    @app.get("/logout")
    async def logout(request: Request):
        token = get_session_token(request)
        if token:
            delete_session(token)
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie("session_token")
        return response

    @app.get("/")
    async def root():
        return RedirectResponse("/dashboard", status_code=302)

    # ── YooKassa Webhook ─────────────────────────────────────────────

    @app.post(settings.WEBHOOK_PATH)
    async def yookassa_webhook(request: Request):
        """
        Обработка webhook уведомлений от ЮKassa.
        Вызывается при изменении статуса платежа.
        После успешной оплаты — активирует подписку, создаёт конфиги
        на всех VPN-серверах и отправляет их пользователю в Telegram.
        """
        from services.yookassa_service import YooKassaService
        from services.subscription_service import SubscriptionService
        from yookassa.domain.notification import WebhookNotificationEventType
        from database import crud

        # Проверяем IP (пропускаем localhost для разработки)
        client_ip = request.client.host
        if client_ip not in ("127.0.0.1", "localhost", "::1") and not YooKassaService.verify_ip(client_ip):
            logger.warning("Webhook от недоверенного IP: %s", client_ip)
            return JSONResponse(status_code=400, content={"error": "Untrusted IP"})

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        try:
            event_type, payment_obj = YooKassaService.parse_notification(body)
        except Exception as e:
            logger.error("Ошибка парсинга webhook: %s", e)
            return JSONResponse(status_code=400, content={"error": str(e)})

        async with async_session_factory() as session:
            try:
                if event_type == WebhookNotificationEventType.PAYMENT_SUCCEEDED:
                    result = await YooKassaService.process_succeeded(session, payment_obj)

                    if result:
                        # Активируем подписку
                        sub = await SubscriptionService.activate_subscription(
                            session=session,
                            user_id=result["user_id"],
                            plan=result["plan"],
                            payment_id=result["payment_id"],
                        )

                        # Создаём конфиги на серверах
                        configs = await SubscriptionService.provision_all_servers(
                            session=session,
                            user_id=result["user_id"],
                            subscription_id=sub.id,
                        )

                        await session.commit()

                        # ── Отправляем конфиги пользователю через Telegram ──
                        user = await crud.get_user_by_id(session, result["user_id"])
                        if user and hasattr(app.state, "bot"):
                            bot = app.state.bot
                            plan_name = settings.plan_names.get(result["plan"], result["plan"])
                            success_count = sum(1 for c in configs if c["success"])

                            try:
                                await bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=(
                                        f"🎉 <b>Подписка активирована!</b>\n\n"
                                        f"📅 План: <b>{plan_name}</b>\n"
                                        f"📆 До: <b>{sub.expires_at.strftime('%d.%m.%Y')}</b>\n"
                                        f"🖥 Серверов: {success_count}/{len(configs)}\n\n"
                                        f"📥 <b>Ваши конфигурации:</b>"
                                    ),
                                    parse_mode="HTML",
                                )

                                from aiogram.types import BufferedInputFile
                                for cfg in configs:
                                    if not cfg["success"]:
                                        continue
                                    server = cfg["server"]
                                    fname = f"amnezia_{server.country_code.lower()}_{server.name.lower()}.conf"
                                    doc = BufferedInputFile(
                                        cfg["config"].encode("utf-8"),
                                        filename=fname,
                                    )
                                    await bot.send_document(
                                        chat_id=user.telegram_id,
                                        document=doc,
                                        caption=(
                                            f"{server.country_flag} <b>{server.name}</b>\n"
                                            f"Импортируйте в Amnezia VPN"
                                        ),
                                        parse_mode="HTML",
                                    )

                                await bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=(
                                        '✅ <b>Готово!</b> Установите <a href="https://amnezia.org">'
                                        "Amnezia VPN</a> и импортируйте конфиги.\n\n"
                                        "Управление подпиской — /start"
                                    ),
                                    parse_mode="HTML",
                                    disable_web_page_preview=True,
                                )
                            except Exception as e:
                                logger.error(
                                    "Не удалось отправить конфиги user=%s: %s",
                                    user.telegram_id, e,
                                )

                        logger.info(
                            "Webhook: подписка активирована для user=%s, "
                            "конфиги отправлены",
                            result["user_id"],
                        )
                    else:
                        await session.commit()

                elif event_type == WebhookNotificationEventType.PAYMENT_CANCELED:
                    await YooKassaService.process_canceled(session, payment_obj)

                    # Уведомляем пользователя об отмене
                    db_payment = await crud.get_payment_by_yookassa_id(
                        session, payment_obj.id,
                    )
                    if db_payment and hasattr(app.state, "bot"):
                        user = await crud.get_user_by_id(session, db_payment.user_id)
                        if user:
                            try:
                                await app.state.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text="❌ Ваш платёж был отменён. Попробуйте снова: /start",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass

                    await session.commit()

                else:
                    await session.commit()

            except Exception as e:
                await session.rollback()
                logger.error("Ошибка обработки webhook: %s", e)
                return JSONResponse(status_code=500, content={"error": str(e)})

        # ЮKassa ожидает 200 OK
        return JSONResponse(status_code=200, content={"status": "ok"})

    return app
