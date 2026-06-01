"""
Handler: Мои подписки — просмотр и скачивание конфигов.
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from bot.keyboards.inline import subscription_keyboard, back_to_menu_keyboard
from services.subscription_service import SubscriptionService
from services.server_manager import ServerManager
import re

logger = logging.getLogger(__name__)
router = Router(name="my_subscriptions")


@router.callback_query(F.data == "my_subscriptions")
async def show_subscriptions(
    callback: CallbackQuery, session: AsyncSession,
) -> None:
    """Показать информацию о подписках пользователя."""
    user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
    if not user:
        try:
            await callback.message.edit_text(
                "❌ Вы не зарегистрированы. Нажмите /start",
                reply_markup=back_to_menu_keyboard(),
            )
        except Exception:
            await callback.message.delete()
            await callback.message.answer(
                "❌ Вы не зарегистрированы. Нажмите /start",
                reply_markup=back_to_menu_keyboard(),
            )
        await callback.answer()
        return

    # Активная подписка
    active_sub = await crud.get_active_subscription(session, user.id)

    if active_sub and active_sub.is_active:
        days_left = (active_sub.expires_at - active_sub.starts_at).days
        plan_name = {
            "7_days": "7 дней (Пробный)",
            "1_month": "1 месяц",
            "3_months": "3 месяца",
            "12_months": "12 месяцев",
        }.get(active_sub.plan, active_sub.plan)

        from datetime import datetime, timezone
        
        # Получаем данные о первом сервере и клиенте
        gb_used = "0.00"
        config_text = ""
        connected_devices = 0
        
        if active_sub.user_servers:
            us = active_sub.user_servers[0]
            config_text = us.config_data
            
            # Извлекаем IP адрес для проверки статистики
            match = re.search(r"Address\s*=\s*([0-9.]+)", config_text)
            if match:
                ip_address = match.group(1)
                server = us.server
                rx, tx = await ServerManager().get_client_traffic(server, ip_address)
                gb_used = f"{(rx + tx) / (1024**3):.2f}"
                
                # Временно простая эвристика для подключенных устройств (если есть трафик -> 1)
                if rx > 0 or tx > 0:
                    connected_devices = 1

        text = (
            "📋 <b>Ваша подписка</b>\n\n"
            f"🟢 Подписка активна до \"{active_sub.expires_at.strftime('%d.%m.%Y')}\"\n\n"
            f"📊 Использовано {gb_used} / 30 ГБ\n"
            f"📱 {connected_devices} / 5 устройств подключено\n\n"
            "🔑 Ключ: Нажмите на код ниже, чтобы скопировать\n\n"
            f"<code>{config_text}</code>"
        )
        try:
            await callback.message.edit_text(
                text, parse_mode="HTML",
                reply_markup=subscription_keyboard(has_configs=True),
            )
        except Exception:
            await callback.message.delete()
            await callback.message.answer(
                text, parse_mode="HTML",
                reply_markup=subscription_keyboard(has_configs=True),
            )
    else:
        # Показать историю подписок
        subs = await crud.get_user_subscriptions(session, user.id)
        if subs:
            text = (
                "📋 <b>Ваши подписки</b>\n\n"
                "❌ Активной подписки нет.\n\n"
                "<b>История:</b>\n"
            )
            for sub in subs[:5]:
                emoji = "✅" if sub.status == "active" else "⏰"
                text += (
                    f"  {emoji} {sub.plan} — "
                    f"{sub.starts_at.strftime('%d.%m')}–"
                    f"{sub.expires_at.strftime('%d.%m.%Y')} "
                    f"({sub.status})\n"
                )
        else:
            text = (
                "📋 <b>Ваши подписки</b>\n\n"
                "У вас ещё нет подписок.\n"
                "Нажмите «Купить VPN» чтобы начать! 🚀"
            )

        try:
            await callback.message.edit_text(
                text, parse_mode="HTML",
                reply_markup=subscription_keyboard(has_configs=False),
            )
        except Exception:
            await callback.message.delete()
            await callback.message.answer(
                text, parse_mode="HTML",
                reply_markup=subscription_keyboard(has_configs=False),
            )

    await callback.answer()


@router.callback_query(F.data == "download_configs")
async def download_configs(
    callback: CallbackQuery, session: AsyncSession,
) -> None:
    """Скачать все конфиги активной подписки."""
    user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    configs = await SubscriptionService.get_user_configs(session, user.id)

    if not configs:
        await callback.answer("❌ Нет активных конфигураций", show_alert=True)
        return

    await callback.answer("📥 Отправляю конфигурации...")

    for cfg in configs:
        filename = f"amnezia_{cfg['country_code'].lower()}_{cfg['server_name'].lower()}.conf"
        file = BufferedInputFile(
            cfg["config_data"].encode("utf-8"),
            filename=filename,
        )
        await callback.message.answer_document(
            document=file,
            caption=(
                f"{cfg['country_flag']} <b>{cfg['server_name']}</b>\n"
                f"Импортируйте в Amnezia VPN"
            ),
            parse_mode="HTML",
        )
