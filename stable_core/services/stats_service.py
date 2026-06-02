"""
Statistics Service — сбор и отображение статистики.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import (
    User, Subscription, Payment, DailyStat,
    SubscriptionStatus, PaymentStatus,
)

logger = logging.getLogger(__name__)


class StatsService:
    """Сервис статистики."""

    @staticmethod
    async def get_dashboard_metrics(session: AsyncSession) -> dict:
        """Получить основные метрики для дашборда."""
        total_users = await crud.count_users(session)
        active_subs = await crud.count_active_subscriptions(session)
        total_revenue = await crud.get_total_revenue(session)

        # Новые пользователи за сегодня
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        stmt = select(func.count(User.id)).where(User.created_at >= today)
        result = await session.execute(stmt)
        new_today = result.scalar_one()

        # Доход за сегодня
        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.paid_at >= today,
        )
        result = await session.execute(stmt)
        revenue_today = Decimal(str(result.scalar_one()))

        return {
            "total_users": total_users,
            "active_subscriptions": active_subs,
            "total_revenue": total_revenue,
            "new_users_today": new_today,
            "revenue_today": revenue_today,
        }

    @staticmethod
    async def collect_daily_stats(session: AsyncSession) -> DailyStat:
        """Собрать статистику за сегодня. Вызывается по расписанию."""
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time()).replace(
            tzinfo=timezone.utc,
        )
        today_end = today_start + timedelta(days=1)

        # Новые пользователи
        stmt = select(func.count(User.id)).where(
            User.created_at >= today_start,
            User.created_at < today_end,
        )
        result = await session.execute(stmt)
        new_users = result.scalar_one()

        # Новые подписки
        stmt = select(func.count(Subscription.id)).where(
            Subscription.created_at >= today_start,
            Subscription.created_at < today_end,
        )
        result = await session.execute(stmt)
        new_subs = result.scalar_one()

        # Доход
        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.paid_at >= today_start,
            Payment.paid_at < today_end,
        )
        result = await session.execute(stmt)
        revenue = Decimal(str(result.scalar_one()))

        # Активные подписки
        active_subs = await crud.count_active_subscriptions(session)

        # Кол-во платежей
        stmt = select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.paid_at >= today_start,
            Payment.paid_at < today_end,
        )
        result = await session.execute(stmt)
        total_payments = result.scalar_one()

        return await crud.upsert_daily_stat(
            session=session,
            stat_date=today,
            new_users=new_users,
            new_subs=new_subs,
            revenue=revenue,
            active_subs=active_subs,
            total_payments=total_payments,
        )

    @staticmethod
    async def get_daily_stats(
        session: AsyncSession, days: int = 30,
    ) -> list[DailyStat]:
        """Получить агрегированную статистику за последние N дней."""
        return await crud.get_daily_stats(session, days=days)
