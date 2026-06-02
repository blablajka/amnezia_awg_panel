"""
Telegram Bot — subscription management & client delivery.

Commands:
  /start — register & welcome
  /plans — show subscription plans
  /status — current subscription & configs
  /config — get VPN config files
  /referral — get referral link
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database.session import async_session_factory
from database import crud
from database.models import SubscriptionStatus

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


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    tg_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    async with async_session_factory() as session:
        user = await crud.get_or_create_user(
            session, tg_id, username=username, full_name=full_name,
        )
        # Handle referral
        ref_arg = message.text.split()[-1] if len(message.text.split()) > 1 else ""
        if ref_arg.startswith("ref") and ref_arg[3:].isdigit():
            ref_id = int(ref_arg[3:])
            if ref_id != user.id and not user.referrer_id:
                user.referrer_id = ref_id
                # Bonus days for referrer
                referrer = await crud.get_user_by_id(session, ref_id)
                if referrer:
                    sub = await crud.get_active_subscription(session, ref_id)
                    if sub:
                        from datetime import timedelta
                        sub.expires_at = sub.expires_at + timedelta(
                            days=settings.REFERRAL_BONUS_DAYS,
                        )
        await session.commit()

    await message.answer(
        f"\U0001f6e1 <b>Smart VPN Panel</b>\n\n"
        f"Welcome, {full_name}!\n"
        f"Plans: /plans\n"
        f"Status: /status",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("plans"))
async def cmd_plans(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{name} - {price}₽",
            callback_data=f"buy:{plan_key}",
        )]
        for plan_key, (name, days, price) in PLAN_DESCRIPTIONS.items()
    ])
    await message.answer(
        "\U0001f4cb <b>Smart VPN Plans:</b>\n\n"
        + "\n".join(
            f"• <b>{name}</b> - {price}₽" if price > 0
            else f"• <b>{name}</b> - Free"
            for _, (name, _, price) in PLAN_DESCRIPTIONS.items()
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("buy:"))
async def handle_buy(callback: types.CallbackQuery):
    plan_key = callback.data.split(":")[1]
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

        now = datetime.now(timezone.utc)
        sub = await crud.create_subscription(
            session, user.id, plan=plan_key,
            starts_at=now,
            expires_at=datetime.fromtimestamp(
                now.timestamp() + days * 86400, tz=timezone.utc,
            ),
        )

        if plan_key == "7_days":
            await session.commit()
            await callback.message.edit_text(
                f"✅ <b>Trial activated!</b>\n"
                f"Plan: {name}\n"
                f"Valid until: {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
                f"Get config: /config",
                parse_mode="HTML",
            )
        else:
            await session.commit()
            await callback.message.edit_text(
                f"\U0001f4b3 <b>Payment required</b>\n"
                f"Plan: {name}\n"
                f"Amount: {price}₽\n\n"
                f"Contact support: {settings.SUPPORT_USERNAME}",
                parse_mode="HTML",
            )
    await callback.answer()


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
            f"\U0001f7e2 <b>Subscription active</b>\n"
            f"Plan: {sub.plan}\n"
            f"Valid until: {sub.expires_at.strftime('%d.%m.%Y %H:%M')}\n"
        )
    else:
        status_text = "\U0001f534 <b>No active subscription</b>\nBuy: /plans\n"

    status_text += f"\nConfigs: {len(configs)}"

    await message.answer(status_text, parse_mode="HTML", reply_markup=main_keyboard())


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
            await message.answer("❌ No active subscription. /plans")
            return
        configs = await crud.get_user_configs(session, user.id, subscription_id=sub.id)

    if not configs:
        await message.answer(
            "\U0001f504 Configs not yet created.\n"
            "Admin will add you to servers manually.\n"
            "Then retry /config",
            reply_markup=main_keyboard(),
        )
        return

    for us in configs:
        await message.answer_document(
            types.BufferedInputFile(
                us.config_data.encode("utf-8"),
                filename=f"{us.client_name}.conf",
            ),
            caption=f"\U0001f511 {us.client_name}\nServer: #{us.server_id}",
        )


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

    ref_link = f"https://t.me/{bot_info.username}?start=ref{user.id}"
    await message.answer(
        f"\U0001f465 <b>Referral Program</b>\n\n"
        f"Invite a friend - get +{settings.REFERRAL_BONUS_DAYS} days!\n\n"
        f"Your link:\n<code>{ref_link}</code>\n\n"
        f"Invited: {ref_count}",
        parse_mode="HTML",
    )


async def start_bot():
    logger.info("Starting Telegram bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))


async def stop_bot():
    logger.info("Stopping Telegram bot...")
    await bot.session.close()
