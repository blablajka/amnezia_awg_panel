"""
Handler: Админ-команды (только для ADMIN_IDS).

Команды:
  /gift <telegram_id> <plan>     — подарить подписку
  /stats                          — быстрая статистика
  /create_promo <code> <discount%> [max_uses]
  /broadcast <текст>              — рассылка всем пользователям
"""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import crud
from services.subscription_service import SubscriptionService
from services.promo_service import PromoService
from services.stats_service import StatsService

logger = logging.getLogger(__name__)
router = Router(name="admin")


# ── Фильтр: только администраторы ────────────────────────────────────────

def is_admin(message: Message) -> bool:
    """Проверка, является ли пользователь администратором."""
    return message.from_user.id in settings.ADMIN_IDS


# ═══════════════════════════════════════════════════════════════════
# /stats — быстрая статистика
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("stats"), is_admin)
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    """Показать основную статистику."""
    metrics = await StatsService.get_dashboard_metrics(session)

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{metrics['total_users']}</b>\n"
        f"🆕 Новых сегодня: <b>{metrics['new_users_today']}</b>\n"
        f"✅ Активных подписок: <b>{metrics['active_subscriptions']}</b>\n"
        f"💰 Общий доход: <b>{metrics['total_revenue']}₽</b>\n"
        f"💵 Доход сегодня: <b>{metrics['revenue_today']}₽</b>\n"
    )
    await message.answer(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
# /gift — подарить подписку
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("gift"), is_admin)
async def cmd_gift(message: Message, session: AsyncSession) -> None:
    """
    Подарить подписку пользователю.
    Использование: /gift <telegram_id> <plan>
    Планы: 1_month, 3_months, 12_months
    """
    args = message.text.split()[1:]

    if len(args) < 2:
        await message.answer(
            "Использование: /gift <telegram_id> <plan>\n"
            "Планы: 1_month, 3_months, 12_months",
        )
        return

    try:
        tg_id = int(args[0])
    except ValueError:
        await message.answer("❌ telegram_id должен быть числом")
        return

    plan = args[1]
    if plan not in settings.plan_days:
        await message.answer(f"❌ Неизвестный план: {plan}")
        return

    user = await crud.get_user_by_telegram_id(session, tg_id)
    if not user:
        await message.answer(f"❌ Пользователь с tg_id={tg_id} не найден")
        return

    # Активируем подписку
    await message.answer(f"⏳ Активирую подписку для @{user.username or tg_id}...")

    sub = await SubscriptionService.activate_subscription(
        session=session,
        user_id=user.id,
        plan=plan,
    )

    # Создаём конфиги на серверах
    configs = await SubscriptionService.provision_all_servers(
        session=session,
        user_id=user.id,
        subscription_id=sub.id,
    )

    success = sum(1 for c in configs if c["success"])
    await message.answer(
        f"✅ Подписка подарена!\n"
        f"👤 Пользователь: @{user.username or tg_id}\n"
        f"📅 План: {plan}\n"
        f"📆 До: {sub.expires_at.strftime('%d.%m.%Y')}\n"
        f"🖥 Серверов: {success}/{len(configs)}",
    )

    # Уведомляем пользователя через бот
    try:
        bot = message.bot
        await bot.send_message(
            chat_id=tg_id,
            text=(
                f"🎁 <b>Вам подарена подписка!</b>\n\n"
                f"📅 План: {settings.plan_names.get(plan, plan)}\n"
                f"📆 До: {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
                f"Используйте «📋 Мои подписки» для получения конфигов."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Не удалось уведомить пользователя %s: %s", tg_id, e)


# ═══════════════════════════════════════════════════════════════════
# /create_promo — создать промокод
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("create_promo"), is_admin)
async def cmd_create_promo(message: Message, session: AsyncSession) -> None:
    """
    Создать промокод.
    Использование: /create_promo <код> <скидка%> [макс_использований]
    """
    args = message.text.split()[1:]

    if len(args) < 2:
        await message.answer(
            "Использование: /create_promo <код> <скидка%> [макс_использований]\n"
            "Пример: /create_promo SALE20 20 100",
        )
        return

    code = args[0]
    try:
        discount = int(args[1])
    except ValueError:
        await message.answer("❌ Скидка должна быть числом (0-100)")
        return

    max_uses = None
    if len(args) >= 3:
        try:
            max_uses = int(args[2])
        except ValueError:
            pass

    promo = await PromoService.create_promo(
        session=session,
        code=code,
        discount_percent=discount,
        max_uses=max_uses,
    )

    await message.answer(
        f"✅ Промокод создан!\n\n"
        f"🏷 Код: <b>{promo.code}</b>\n"
        f"📉 Скидка: <b>{promo.discount_percent}%</b>\n"
        f"🔢 Макс. использований: <b>{promo.max_uses or '∞'}</b>",
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
# /broadcast — рассылка
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("broadcast"), is_admin)
async def cmd_broadcast(message: Message, session: AsyncSession) -> None:
    """
    Рассылка сообщения всем пользователям.
    Использование: /broadcast <текст>
    """
    text = message.text.replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer("Использование: /broadcast <текст сообщения>")
        return

    users = await crud.get_all_users(session, limit=10000)
    sent = 0
    failed = 0
    bot = message.bot

    await message.answer(f"⏳ Рассылка {len(users)} пользователям...")

    for user in users:
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                parse_mode="HTML",
            )
            sent += 1
        except Exception:
            failed += 1

    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"📤 Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}",
    )
