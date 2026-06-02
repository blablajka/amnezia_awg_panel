"""
Telegram Bot — подписки, оплата, выдача конфигов.

Commands:
  /start    — регистрация
  /plans    — выбор и покупка подписки
  /status   — статус подписки
  /config   — получить .conf файлы
  /referral — реферальная ссылка
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database.session import async_session_factory
from database import crud
from database.models import PaymentStatus

logger = logging.getLogger(__name__)

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

PLAN_DESCRIPTIONS = {
    "7_days": ("7 дней (Пробный)", 7, 0),
    "1_month": ("1 месяц", 30, settings.PRICE_1_MONTH),
    "3_months": ("3 месяца", 90, settings.PRICE_3_MONTHS),
    "12_months": ("12 месяцев", 365, settings.PRICE_12_MONTHS),
}


def main_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="/plans"),
             types.KeyboardButton(text="/status")],
            [types.KeyboardButton(text="/config"),
             types.KeyboardButton(text="/referral")],
        ],
        resize_keyboard=True,
    )


# ═══════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    tg_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    async with async_session_factory() as session:
        user = await crud.get_or_create_user(
            session, tg_id, username=username, full_name=full_name,
        )
        # Referral handling
        parts = message.text.split()
        if len(parts) > 1:
            ref_arg = parts[-1]
            if ref_arg.startswith("ref") and ref_arg[3:].isdigit():
                ref_id = int(ref_arg[3:])
                if ref_id != user.id and not user.referrer_id:
                    user.referrer_id = ref_id
                    referrer = await crud.get_user_by_id(session, ref_id)
                    if referrer:
                        sub = await crud.get_active_subscription(session, ref_id)
                        if sub:
                            sub.expires_at = sub.expires_at + timedelta(
                                days=settings.REFERRAL_BONUS_DAYS,
                            )
        await session.commit()

    await message.answer(
        "\U0001f6e1 <b>Smart VPN Panel</b>\n\n"
        "Welcome, %s!\n\n"
        "Available plans: /plans\n"
        "Subscription status: /status\n"
        "Get config: /config" % full_name,
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════
# /plans
# ═══════════════════════════════════════════════════════════════

@dp.message(Command("plans"))
async def cmd_plans(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="%s — %s₽" % (name, price) if price > 0 else "%s — Free" % name,
            callback_data="buy:%s" % plan_key,
        )]
        for plan_key, (name, days, price) in PLAN_DESCRIPTIONS.items()
    ])
    lines = ["\U0001f4cb <b>VPN Plans:</b>\n"]
    for plan_key, (name, days, price) in PLAN_DESCRIPTIONS.items():
        if price > 0:
            lines.append("• <b>%s</b> — %d₽ (%d days)" % (name, price, days))
        else:
            lines.append("• <b>%s</b> — Free (%d days)" % (name, days))

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ═══════════════════════════════════════════════════════════════
# Buy flow (inline callback)
# ═══════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("buy:"))
async def handle_buy(callback: types.CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]
    plan_info = PLAN_DESCRIPTIONS.get(plan_key)
    if not plan_info:
        await callback.answer("Unknown plan")
        return

    name, days, price = plan_info
    tg_id = callback.from_user.id

    async with async_session_factory() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Use /start first")
            return

        # Already has active subscription?
        existing = await crud.get_active_subscription(session, user.id)
        if existing:
            await callback.message.edit_text(
                "⚠️ You already have an active subscription.\n"
                "Valid until: %s\n\n"
                "Status: /status | Config: /config" % existing.expires_at.strftime("%d.%m.%Y"),
                parse_mode="HTML",
            )
            await callback.answer()
            return

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=days)

        if plan_key == "7_days":
            # Trial: activate immediately + provision
            sub = await crud.create_subscription(
                session, user.id, plan=plan_key,
                starts_at=now, expires_at=expires_at,
            )
            await session.commit()

            await callback.message.edit_text(
                "✅ <b>Trial activated!</b>\n"
                "Plan: %s\n"
                "Valid until: %s\n\n"
                "⏳ Provisioning servers..." % (name, sub.expires_at.strftime("%d.%m.%Y")),
                parse_mode="HTML",
            )
            await callback.answer()
            await _provision_and_deliver(callback.message, session, user.id, sub.id)

        else:
            # Paid plan: create subscription as PENDING, activate on payment
            import secrets
            payment_id = "test_%s_%s" % (tg_id, secrets.token_hex(4))
            amount = Decimal(str(price))

            payment = await crud.create_payment(
                session, user_id=user.id,
                yookassa_payment_id=payment_id,
                amount=amount, plan=plan_key,
            )
            sub = await crud.create_subscription(
                session, user.id, plan=plan_key,
                starts_at=now, expires_at=expires_at,
            )
            # Set subscription as pending until payment confirmed
            from database.models import SubscriptionStatus
            sub.status = SubscriptionStatus.CANCELLED.value  # Not active yet
            payment.subscription_id = sub.id
            await session.commit()

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="💳 Pay %d₽ (Test Mode)" % price,
                    callback_data="pay:%s:%s" % (payment_id, sub.id),
                )],
                [InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="cancel_pay:%s" % payment_id,
                )],
            ])

            await callback.message.edit_text(
                "💰 <b>%s</b>\n"
                "Amount: <b>%d₽</b>\n"
                "Duration: <b>%d days</b>\n\n"
                "<i>Test mode: payment auto-confirms.</i>" % (name, price, days),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await callback.answer()


# ═══════════════════════════════════════════════════════════════
# Payment confirmation (placeholder — auto-succeeds after 2s)
# ═══════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("pay:"))
async def handle_pay(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    payment_id = parts[1]
    sub_id = int(parts[2])

    await callback.message.edit_text(
        "⏳ <b>Processing payment...</b>\n\n"
        "<i>Test mode: confirming in 2s...</i>",
        parse_mode="HTML",
    )
    await callback.answer()
    await asyncio.sleep(2)

    async with async_session_factory() as session:
        await crud.update_payment_status(
            session, yookassa_payment_id=payment_id,
            status=PaymentStatus.SUCCEEDED.value,
            subscription_id=sub_id,
        )
        sub = await crud.get_subscription_by_id(session, sub_id)
        if not sub:
            await callback.message.edit_text("❌ Subscription not found.")
            await callback.answer()
            return

        # Activate subscription now that payment is confirmed
        from database.models import SubscriptionStatus
        sub.status = SubscriptionStatus.ACTIVE.value
        user_id = sub.user_id
        await session.commit()

    async with async_session_factory() as session:
        await _provision_and_deliver(callback.message, session, user_id, sub_id)


@dp.callback_query(F.data.startswith("cancel_pay:"))
async def handle_cancel_pay(callback: types.CallbackQuery):
    payment_id = callback.data.split(":", 1)[1]
    async with async_session_factory() as session:
        await crud.update_payment_status(
            session, yookassa_payment_id=payment_id,
            status=PaymentStatus.CANCELED.value,
        )
        await session.commit()

    await callback.message.edit_text("❌ Payment cancelled.\nChoose a plan: /plans")
    await callback.answer()


# ═══════════════════════════════════════════════════════════════
# Provision servers + deliver configs to user
# ═══════════════════════════════════════════════════════════════

async def _provision_and_deliver(
    message: types.Message,
    session,
    user_id: int,
    subscription_id: int,
):
    from services.subscription_service import SubscriptionService

    try:
        results = await SubscriptionService.provision_all_servers(
            session, user_id, subscription_id,
        )
        await session.commit()

        configs = []
        errors = []
        for r in results:
            if r.get("success"):
                configs.append(r)
            else:
                errors.append(r)

        if configs:
            lines = ["✅ <b>VPN is ready!</b>\n"]
            for c in configs:
                srv = c.get("server")
                srv_name = srv.name if srv else "Unknown"
                flag = srv.country_flag if srv else "🌍"
                lines.append("%s <b>%s</b>" % (flag, srv_name))
                us = c.get("user_server")
                conf_text = us.config_data if us else c.get("config", "")
                client_name = us.client_name if us else "vpn"
                await message.answer_document(
                    types.BufferedInputFile(
                        conf_text.encode("utf-8"),
                        filename="%s.conf" % client_name,
                    ),
                    caption="%s %s — %s" % (flag, srv_name, client_name),
                )
            if errors:
                lines.append("\n⚠️ %d server(s) failed." % len(errors))
            await message.answer(
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
        else:
            await message.answer(
                "⚠️ No servers available for provisioning.\n"
                "Contact support: %s" % settings.SUPPORT_USERNAME,
                reply_markup=main_keyboard(),
            )
    except Exception as e:
        logger.error("Provision failed for user %s sub %s: %s", user_id, subscription_id, e)
        await message.answer(
            "❌ Provisioning failed: %s\n"
            "Contact support: %s" % (str(e)[:200], settings.SUPPORT_USERNAME),
            reply_markup=main_keyboard(),
        )


# ═══════════════════════════════════════════════════════════════
# /status
# ═══════════════════════════════════════════════════════════════

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    tg_id = message.from_user.id
    async with async_session_factory() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Use /start first")
            return
        sub = await crud.get_active_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)

    if sub:
        status_text = (
            "🟢 <b>Subscription active</b>\n"
            "Plan: %s\n"
            "Valid until: %s\n"
        ) % (sub.plan, sub.expires_at.strftime("%d.%m.%Y %H:%M"))
    else:
        status_text = "🔴 <b>No active subscription</b>\nBuy: /plans\n"

    status_text += "\nConfigs: %d" % len(configs)
    await message.answer(status_text, parse_mode="HTML", reply_markup=main_keyboard())


# ═══════════════════════════════════════════════════════════════
# /config
# ═══════════════════════════════════════════════════════════════

@dp.message(Command("config"))
async def cmd_config(message: types.Message):
    tg_id = message.from_user.id
    async with async_session_factory() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Use /start first")
            return
        sub = await crud.get_active_subscription(session, user.id)
        if not sub:
            await message.answer("❌ No active subscription.\n/plans")
            return
        configs = await crud.get_user_configs(session, user.id, subscription_id=sub.id)

    if not configs:
        await message.answer(
            "🔄 Configs not generated yet.\n"
            "Use /status to check, then /config to retry.\n"
            "If issue persists, contact %s" % settings.SUPPORT_USERNAME,
            reply_markup=main_keyboard(),
        )
        return

    for us in configs:
        await message.answer_document(
            types.BufferedInputFile(
                us.config_data.encode("utf-8"),
                filename="%s.conf" % us.client_name,
            ),
            caption="🔑 %s\nServer: #%d" % (us.client_name, us.server_id),
        )


# ═══════════════════════════════════════════════════════════════
# /referral
# ═══════════════════════════════════════════════════════════════

@dp.message(Command("referral"))
async def cmd_referral(message: types.Message):
    tg_id = message.from_user.id
    bot_info = await bot.me()
    async with async_session_factory() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Use /start first")
            return
        ref_count = await crud.count_referrals(session, user.id)

    ref_link = "https://t.me/%s?start=ref%s" % (bot_info.username, user.id)
    await message.answer(
        "👥 <b>Referral Program</b>\n\n"
        "Invite a friend — get +%d days!\n\n"
        "Your link:\n<code>%s</code>\n\n"
        "Invited: %d" % (settings.REFERRAL_BONUS_DAYS, ref_link, ref_count),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════
# Lifecycle
# ═══════════════════════════════════════════════════════════════

async def start_bot():
    logger.info("Starting Telegram bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))


async def stop_bot():
    logger.info("Stopping Telegram bot...")
    await bot.session.close()
