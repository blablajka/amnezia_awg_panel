"""
CRUD операции для всех моделей.
Все функции принимают AsyncSession и работают асинхронно.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import select, func, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import (
    User, Subscription, Server, UserServer,
    Payment, PromoCode, DailyStat, Bridge,
    SubscriptionStatus, PaymentStatus,
)


# ═══════════════════════════════════════════════════════════════════
# User CRUD
# ═══════════════════════════════════════════════════════════════════

async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    full_name: str = "",
    referrer_id: int | None = None,
) -> User:
    """Найти пользователя по telegram_id или создать нового."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            referrer_id=referrer_id,
        )
        session.add(user)
        await session.flush()
    else:
        # Обновить username/full_name если изменились
        if username and user.username != username:
            user.username = username
        if full_name and user.full_name != full_name:
            user.full_name = full_name
    return user


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int,
) -> User | None:
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_all_users(
    session: AsyncSession, limit: int = 100, offset: int = 0,
) -> list[User]:
    stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_users(session: AsyncSession) -> int:
    stmt = select(func.count(User.id))
    result = await session.execute(stmt)
    return result.scalar_one()


async def count_referrals(session: AsyncSession, user_id: int) -> int:
    """Количество пользователей, приглашённых данным пользователем."""
    stmt = select(func.count(User.id)).where(User.referrer_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one()


# ═══════════════════════════════════════════════════════════════════
# Subscription CRUD
# ═══════════════════════════════════════════════════════════════════

async def create_subscription(
    session: AsyncSession,
    user_id: int,
    plan: str,
    starts_at: datetime,
    expires_at: datetime,
) -> Subscription:
    sub = Subscription(
        user_id=user_id,
        plan=plan,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=starts_at,
        expires_at=expires_at,
    )
    session.add(sub)
    await session.flush()
    return sub


async def get_active_subscription(
    session: AsyncSession, user_id: int,
) -> Subscription | None:
    now = datetime.now(timezone.utc)
    stmt = (
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_subscriptions(
    session: AsyncSession, user_id: int,
) -> list[Subscription]:
    stmt = (
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def expire_subscriptions(session: AsyncSession) -> list[Subscription]:
    """Пометить все просроченные подписки как expired. Вернуть список."""
    now = datetime.now(timezone.utc)
    
    # 1. Найти просроченные подписки
    stmt = select(Subscription).where(
        Subscription.status == SubscriptionStatus.ACTIVE.value,
        Subscription.expires_at <= now,
    )
    result = await session.execute(stmt)
    expired = list(result.scalars().all())
    
    # 2. Пометить их как expired
    for sub in expired:
        sub.status = SubscriptionStatus.EXPIRED.value
        
    await session.commit()
    return expired


async def count_active_subscriptions(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    stmt = select(func.count(Subscription.id)).where(
        Subscription.status == SubscriptionStatus.ACTIVE.value,
        Subscription.expires_at > now,
    )
    result = await session.execute(stmt)
    return result.scalar_one()


# ═══════════════════════════════════════════════════════════════════
# Server CRUD
# ═══════════════════════════════════════════════════════════════════

async def get_active_servers(session: AsyncSession) -> list[Server]:
    stmt = select(Server).where(Server.is_active == True).order_by(Server.id)  # noqa: E712
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_all_servers(session: AsyncSession) -> list[Server]:
    stmt = select(Server).order_by(Server.id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_server_by_id(session: AsyncSession, server_id: int) -> Server | None:
    return await session.get(Server, server_id)


async def create_server(
    session: AsyncSession, name: str, host: str, country_code: str, **kwargs,
) -> Server:
    server = Server(name=name, host=host, country_code=country_code, **kwargs)
    session.add(server)
    await session.flush()
    return server


# ═══════════════════════════════════════════════════════════════════
# UserServer CRUD
# ═══════════════════════════════════════════════════════════════════

async def create_user_server(
    session: AsyncSession,
    user_id: int,
    server_id: int,
    subscription_id: int,
    client_name: str,
    config_data: str,
) -> UserServer:
    us = UserServer(
        user_id=user_id,
        server_id=server_id,
        subscription_id=subscription_id,
        client_name=client_name,
        config_data=config_data,
    )
    session.add(us)
    await session.flush()
    return us


async def get_user_configs(
    session: AsyncSession, user_id: int, subscription_id: int | None = None,
) -> list[UserServer]:
    conditions = [UserServer.user_id == user_id, UserServer.is_active == True]  # noqa: E712
    if subscription_id:
        conditions.append(UserServer.subscription_id == subscription_id)
    stmt = select(UserServer).where(and_(*conditions))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def deactivate_user_servers(
    session: AsyncSession, subscription_id: int,
) -> None:
    stmt = (
        update(UserServer)
        .where(UserServer.subscription_id == subscription_id)
        .values(is_active=False)
    )
    await session.execute(stmt)


# ═══════════════════════════════════════════════════════════════════
# Payment CRUD
# ═══════════════════════════════════════════════════════════════════

async def create_payment(
    session: AsyncSession,
    user_id: int,
    yookassa_payment_id: str,
    amount: Decimal,
    plan: str,
    confirmation_url: str | None = None,
    promo_code_id: int | None = None,
) -> Payment:
    payment = Payment(
        user_id=user_id,
        yookassa_payment_id=yookassa_payment_id,
        amount=amount,
        plan=plan,
        confirmation_url=confirmation_url,
        promo_code_id=promo_code_id,
    )
    session.add(payment)
    await session.flush()
    return payment


async def get_payment_by_yookassa_id(
    session: AsyncSession, yookassa_payment_id: str,
) -> Payment | None:
    stmt = select(Payment).where(Payment.yookassa_payment_id == yookassa_payment_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_payment_status(
    session: AsyncSession,
    yookassa_payment_id: str,
    status: str,
    subscription_id: int | None = None,
) -> Payment | None:
    payment = await get_payment_by_yookassa_id(session, yookassa_payment_id)
    if payment:
        payment.status = status
        if status == PaymentStatus.SUCCEEDED.value:
            payment.paid_at = datetime.now(timezone.utc)
        if subscription_id:
            payment.subscription_id = subscription_id
    return payment


async def get_all_payments(
    session: AsyncSession, limit: int = 100, offset: int = 0,
) -> list[Payment]:
    stmt = select(Payment).order_by(Payment.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════════
# PromoCode CRUD
# ═══════════════════════════════════════════════════════════════════

async def get_promo_by_code(
    session: AsyncSession, code: str,
) -> PromoCode | None:
    stmt = select(PromoCode).where(PromoCode.code == code.upper())
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_promo_code(
    session: AsyncSession,
    code: str,
    discount_percent: int = 0,
    discount_amount: Decimal | None = None,
    max_uses: int | None = None,
    valid_until: datetime | None = None,
) -> PromoCode:
    promo = PromoCode(
        code=code.upper(),
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        max_uses=max_uses,
        valid_until=valid_until,
    )
    session.add(promo)
    await session.flush()
    return promo


async def increment_promo_usage(session: AsyncSession, promo_id: int) -> None:
    stmt = (
        update(PromoCode)
        .where(PromoCode.id == promo_id)
        .values(current_uses=PromoCode.current_uses + 1)
    )
    await session.execute(stmt)


async def get_all_promo_codes(session: AsyncSession) -> list[PromoCode]:
    stmt = select(PromoCode).order_by(PromoCode.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════════
# DailyStat CRUD
# ═══════════════════════════════════════════════════════════════════

async def upsert_daily_stat(
    session: AsyncSession,
    stat_date: date,
    new_users: int = 0,
    new_subs: int = 0,
    revenue: Decimal = Decimal("0"),
    active_subs: int = 0,
    total_payments: int = 0,
) -> DailyStat:
    stmt = select(DailyStat).where(DailyStat.date == stat_date)
    result = await session.execute(stmt)
    stat = result.scalar_one_or_none()
    if stat is None:
        stat = DailyStat(
            date=stat_date, new_users=new_users,
            new_subscriptions=new_subs, revenue=revenue,
            active_subscriptions=active_subs, total_payments=total_payments,
        )
        session.add(stat)
    else:
        stat.new_users = new_users
        stat.new_subscriptions = new_subs
        stat.revenue = revenue
        stat.active_subscriptions = active_subs
        stat.total_payments = total_payments
    await session.flush()
    return stat


async def get_daily_stats(
    session: AsyncSession, days: int = 30,
) -> list[DailyStat]:
    stmt = select(DailyStat).order_by(DailyStat.date.desc()).limit(days)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_total_revenue(session: AsyncSession) -> Decimal:
    stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status == PaymentStatus.SUCCEEDED.value,
    )
    result = await session.execute(stmt)
    return Decimal(str(result.scalar_one()))


# ═══════════════════════════════════════════════════════════════════
# Bridge CRUD
# ═══════════════════════════════════════════════════════════════════

async def get_all_bridges(session: AsyncSession) -> list[Bridge]:
    stmt = select(Bridge).order_by(Bridge.id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_bridge_by_id(session: AsyncSession, bridge_id: int) -> Bridge | None:
    return await session.get(Bridge, bridge_id)


async def get_active_bridges(session: AsyncSession) -> list[Bridge]:
    stmt = select(Bridge).where(Bridge.is_active == True).order_by(Bridge.id)  # noqa: E712
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_bridge(
    session: AsyncSession, server_from_id: int, server_to_id: int,
    protocol: str = "gost", config_data: str | None = None,
) -> Bridge:
    bridge = Bridge(
        server_from_id=server_from_id, server_to_id=server_to_id,
        protocol=protocol, config_data=config_data,
    )
    session.add(bridge)
    await session.flush()
    return bridge


async def update_bridge_status(
    session: AsyncSession, bridge_id: int, is_active: bool,
) -> None:
    stmt = update(Bridge).where(Bridge.id == bridge_id).values(is_active=is_active)
    await session.execute(stmt)


async def delete_bridge(session: AsyncSession, bridge_id: int) -> bool:
    bridge = await session.get(Bridge, bridge_id)
    if bridge:
        await session.delete(bridge)
        return True
    return False
