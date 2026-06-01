"""
Amnezia VPN System — Main Entry Point.

Запускает Telegram бот (polling) и Web Admin Panel (uvicorn)
параллельно в одном asyncio event loop.
"""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from config import settings
from database.session import init_db
from bot.handlers import start, buy_subscription, my_subscriptions, admin
from bot.middlewares.db_middleware import DatabaseMiddleware
from web.main import create_web_app

# ── Logging ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("amnezia_vpn")


# ── Bot Setup ────────────────────────────────────────────────────────────


def create_bot() -> tuple[Bot, Dispatcher]:
    """Создать и настроить Telegram бота."""
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:
        storage = RedisStorage.from_url(settings.REDIS_URL)
        logger.info("Используется RedisStorage для FSM")
    except Exception:
        storage = MemoryStorage()
        logger.warning("Redis недоступен, используется MemoryStorage для FSM")

    dp = Dispatcher(storage=storage)

    # Регистрация middleware для всех типов событий
    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())

    # Регистрация роутеров (handlers)
    dp.include_router(start.router)
    dp.include_router(buy_subscription.router)
    dp.include_router(my_subscriptions.router)
    dp.include_router(admin.router)

    return bot, dp


# ── Web Setup ────────────────────────────────────────────────────────────


async def run_web_server(app) -> None:
    """Запустить FastAPI через uvicorn programmatically."""
    config = uvicorn.Config(
        app,
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


# ── Scheduler ────────────────────────────────────────────────────────────


async def run_scheduler(bot: Bot) -> None:
    """Фоновые задачи: деактивация подписок, сбор статистики, мониторинг."""
    from database.session import async_session_factory
    from services.subscription_service import SubscriptionService
    from services.stats_service import StatsService
    from services.server_manager import ServerManager
    from database import crud

    server_manager = ServerManager()
    # Отслеживание простоев серверов: {server_id: (downtime_start, alerted)}
    server_downtime: dict[int, tuple[float, bool]] = {}

    while True:
        try:
            async with async_session_factory() as session:
                # Деактивировать просроченные подписки
                expired = await SubscriptionService.deactivate_expired(session)
                if expired > 0:
                    logger.info("Деактивировано %d подписок", expired)

                # Собрать дневную статистику
                await StatsService.collect_daily_stats(session)

                # ── Мониторинг серверов ──────────────────────────
                servers = await crud.get_active_servers(session)
                for srv in servers:

                    monitoring = await server_manager.get_full_monitoring(srv)

                    if not monitoring.get("online"):
                        # Сервер оффлайн
                        now_ts = asyncio.get_event_loop().time()
                        if srv.id not in server_downtime:
                            server_downtime[srv.id] = (now_ts, False)
                        elif not server_downtime[srv.id][1] and (now_ts - server_downtime[srv.id][0]) > 300:
                            # Оффлайн > 5 минут — алерт
                            for admin_id in settings.ADMIN_IDS:
                                try:
                                    await bot.send_message(
                                        admin_id,
                                        f"🚨 <b>Сервер недоступен!</b>\n"
                                        f"{srv.country_flag} {srv.name} ({srv.host})\n"
                                        f"Ошибка: {monitoring.get('error', 'Нет ответа')}",
                                        parse_mode="HTML",
                                    )
                                except Exception:
                                    pass
                            server_downtime[srv.id] = (server_downtime[srv.id][0], True)
                    else:
                        # Сервер снова онлайн
                        if srv.id in server_downtime and server_downtime[srv.id][1]:
                            for admin_id in settings.ADMIN_IDS:
                                try:
                                    await bot.send_message(
                                        admin_id,
                                        f"✅ <b>Сервер восстановлен!</b>\n"
                                        f"{srv.country_flag} {srv.name} ({srv.host})",
                                        parse_mode="HTML",
                                    )
                                except Exception:
                                    pass
                        server_downtime.pop(srv.id, None)

                        # Проверка количества клиентов
                        peers_str = monitoring.get("awg_peers", "0")
                        try:
                            peers = int(peers_str)
                            if peers > 200:
                                for admin_id in settings.ADMIN_IDS:
                                    try:
                                        await bot.send_message(
                                            admin_id,
                                            f"⚠️ <b>На сервере заканчиваются IP-адреса!</b>\n"
                                            f"{srv.country_flag} {srv.name}: <b>{peers}/253</b> клиентов",
                                            parse_mode="HTML",
                                        )
                                    except Exception:
                                        pass
                        except (ValueError, TypeError):
                            pass

                        # Проверка места на диске
                        disk_str = monitoring.get("disk", "")
                        disk_parts = disk_str.split()
                        if len(disk_parts) >= 5:
                            try:
                                used_pct = int(disk_parts[4].replace("%", ""))
                                if used_pct > 90:
                                    for admin_id in settings.ADMIN_IDS:
                                        try:
                                            await bot.send_message(
                                                admin_id,
                                                f"💾 <b>Мало места на диске!</b>\n"
                                                f"{srv.country_flag} {srv.name}: занято <b>{used_pct}%</b>\n"
                                                f"Доступно: {disk_parts[3]}",
                                                parse_mode="HTML",
                                            )
                                        except Exception:
                                            pass
                            except (ValueError, IndexError):
                                pass

                await session.commit()
        except Exception as e:
            logger.error("Ошибка scheduler: %s", e)

        # Каждые 5 минут
        await asyncio.sleep(300)


# ── Main ─────────────────────────────────────────────────────────────────


async def main() -> None:
    """Главная точка входа — запуск бота + веб + scheduler."""
    logger.info("=" * 60)
    logger.info("  Amnezia VPN System Starting...")
    logger.info("=" * 60)

    # Инициализация БД (создание таблиц)
    await init_db()
    logger.info("✅ База данных инициализирована")

    # Создание бота
    bot, dp = create_bot()
    logger.info("✅ Telegram бот создан")

    # Создание веб-приложения
    web_app = create_web_app()
    web_app.state.bot = bot  # Для отправки уведомлений из webhook
    logger.info("✅ Web Admin Panel создана")

    logger.info(
        "🌐 Web Panel: http://%s:%s", settings.WEB_HOST, settings.WEB_PORT,
    )
    logger.info("🤖 Telegram Bot: polling mode")
    logger.info("📡 YooKassa webhook: %s", settings.webhook_url)

    # Запуск всех задач параллельно
    try:
        await asyncio.gather(
            dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
            run_web_server(web_app),
            run_scheduler(bot),
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
