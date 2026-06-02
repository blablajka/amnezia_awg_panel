"""
Subscription Service — бизнес-логика подписок.

Активация, продление, деактивация подписок.
Provision клиентов на всех VPN серверах.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import crud
from database.models import Subscription
from services.server_manager import ServerManager
from services.protocols import get_protocol_handler

logger = logging.getLogger(__name__)

server_manager = ServerManager()


class SubscriptionService:
    """Сервис управления подписками."""

    @staticmethod
    async def activate_subscription(
        session: AsyncSession,
        user_id: int,
        plan: str,
        payment_id: int | None = None,
    ) -> Subscription:
        """
        Активировать новую подписку для пользователя.
        Если есть активная подписка — продлить.
        """
        now = datetime.now(timezone.utc)
        days = settings.plan_days.get(plan, 30)

        # Проверяем, есть ли активная подписка (для продления)
        existing = await crud.get_active_subscription(session, user_id)
        if existing and existing.is_active:
            # Продлеваем: добавляем дни к текущему expires_at
            starts_at = existing.expires_at
            expires_at = starts_at + timedelta(days=days)
            existing.expires_at = expires_at
            sub = existing
            logger.info(
                "Подписка продлена: user=%s, plan=%s, до %s",
                user_id, plan, expires_at,
            )
        else:
            # Создаём новую
            starts_at = now
            expires_at = now + timedelta(days=days)
            sub = await crud.create_subscription(
                session=session,
                user_id=user_id,
                plan=plan,
                starts_at=starts_at,
                expires_at=expires_at,
            )
            logger.info(
                "Подписка создана: user=%s, plan=%s, до %s",
                user_id, plan, expires_at,
            )

        # Обновляем ссылку на subscription в платеже
        if payment_id:
            await crud.update_payment_status(
                session, "", "succeeded", subscription_id=sub.id,
            )

        return sub

    @staticmethod
    async def provision_all_servers(
        session: AsyncSession,
        user_id: int,
        subscription_id: int,
    ) -> list[dict]:
        """
        Создать клиентов на ВСЕХ активных серверах.
        Возвращает список словарей с информацией о каждом конфиге.
        """
        servers = await crud.get_active_servers(session)
        if not servers:
            logger.warning("Нет активных серверов для provisioning")
            return []

        user = await crud.get_user_by_id(session, user_id)
        if not user:
            raise ValueError(f"Пользователь {user_id} не найден")

        # Уникальное имя клиента на основе telegram_id и subscription_id
        client_name = f"tg{user.telegram_id}_sub{subscription_id}"

        results = []
        for server in servers:
            try:
                # Используем протокольный хендлер сервера
                protocol = getattr(server, "protocol", "awg") or "awg"
                handler = get_protocol_handler(protocol)
                result = await handler.create_client(
                    server=server,
                    client_name=client_name,
                )
                
                if isinstance(result, tuple) and len(result) == 2:
                    config_data, client_id = result
                    actual_client_name = client_id
                else:
                    config_data = result
                    actual_client_name = client_name

                # Сохраняем в БД
                user_server = await crud.create_user_server(
                    session=session,
                    user_id=user_id,
                    server_id=server.id,
                    subscription_id=subscription_id,
                    client_name=actual_client_name,
                    config_data=config_data,
                )

                results.append({
                    "server": server,
                    "user_server": user_server,
                    "config": config_data,
                    "success": True,
                })
                logger.info(
                    "Provisioned %s on %s %s",
                    client_name, server.country_flag, server.name,
                )

            except Exception as e:
                logger.error(
                    "Ошибка provisioning на %s: %s", server.name, e,
                )
                results.append({
                    "server": server,
                    "config": None,
                    "success": False,
                    "error": str(e),
                })

        return results

    @staticmethod
    async def deactivate_expired(session: AsyncSession) -> int:
        """
        Деактивировать все просроченные подписки и удалить клиентов с серверов.
        Вызывается по расписанию (cron).
        """
        expired_subs = await crud.expire_subscriptions(session)
        count = len(expired_subs)
        
        if count > 0:
            logger.info("Деактивировано %d просроченных подписок", count)
            # Удаляем клиентов с серверов
            for sub in expired_subs:
                user_servers = await crud.get_user_configs(session, sub.user_id, subscription_id=sub.id)
                for us in user_servers:
                    server = us.server
                    try:
                        protocol = getattr(server, "protocol", "awg") or "awg"
                        handler = get_protocol_handler(protocol)
                        # client_name содержит идентификатор клиента
                        await handler.remove_client(server, us.client_name)
                        logger.info("Удален клиент %s с сервера %s (подписка %s истекла)", us.client_name, server.name, sub.id)
                    except Exception as e:
                        logger.error("Ошибка при удалении клиента %s с сервера %s: %s", us.client_name, server.name, e)
                    finally:
                        # Удаляем запись из БД
                        await session.delete(us)
            await session.commit()
            
        return count

    @staticmethod
    async def get_user_configs(
        session: AsyncSession, user_id: int,
    ) -> list[dict]:
        """Получить все активные конфиги пользователя."""
        active_sub = await crud.get_active_subscription(session, user_id)
        if not active_sub:
            return []

        user_servers = await crud.get_user_configs(
            session, user_id, subscription_id=active_sub.id,
        )

        results = []
        for us in user_servers:
            server = us.server
            results.append({
                "server_name": server.name,
                "country_code": server.country_code,
                "country_flag": server.country_flag,
                "config_data": us.config_data,
                "client_name": us.client_name,
            })
        return results
