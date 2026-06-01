"""
Inline-клавиатуры для Telegram бота.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings


def main_menu_keyboard(has_active_subscription: bool = False) -> InlineKeyboardMarkup:
    """Главное меню бота."""
    builder = InlineKeyboardBuilder()
    if has_active_subscription:
        builder.row(
            InlineKeyboardButton(text="⚡ Подключиться", callback_data="connect_instructions"),
        )
        builder.row(
            InlineKeyboardButton(text="Моя подписка", callback_data="my_subscriptions"),
        )
    else:
        builder.row(
            InlineKeyboardButton(text="🎁 Активировать 7 дней", callback_data="activate_trial"),
        )
        builder.row(
            InlineKeyboardButton(text="🔑 Продлить подписку", callback_data="buy_vpn"),
        )

    builder.row(
        InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="invite_friend"),
    )
    builder.row(
        InlineKeyboardButton(text="Поддержка", callback_data="support"),
        InlineKeyboardButton(text="Правила", callback_data="rules"),
    )
    return builder.as_markup()


def platforms_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора платформы."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🪟 Windows", callback_data="platform_windows"),
        InlineKeyboardButton(text="🐧 Linux", callback_data="platform_linux"),
    )
    builder.row(
        InlineKeyboardButton(text="🤖 Android", callback_data="platform_android"),
        InlineKeyboardButton(text="🍏 iOS", callback_data="platform_ios"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_to_menu"),
    )
    return builder.as_markup()


def plan_selection_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифного плана."""
    builder = InlineKeyboardBuilder()
    prices = settings.prices
    names = settings.plan_names

    builder.row(
        InlineKeyboardButton(
            text=f"📅 {names['1_month']} — {prices['1_month']}₽",
            callback_data="plan:1_month",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"📅 {names['3_months']} — {prices['3_months']}₽ (-21%)",
            callback_data="plan:3_months",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"🟢 {names['12_months']} — {prices['12_months']}₽ (выгода 40%)",
            callback_data="plan:12_months",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu"),
    )
    return builder.as_markup()


def promo_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для ввода промокода."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_promo"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="buy_vpn"),
    )
    return builder.as_markup()


def email_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для ввода email."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_email"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="buy_vpn"),
    )
    return builder.as_markup()


def payment_keyboard(confirmation_url: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой оплаты."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url),
    )
    builder.row(
        InlineKeyboardButton(
            text="✅ Я оплатил", callback_data="check_payment",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_payment"),
    )
    return builder.as_markup()


def subscription_keyboard(has_configs: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура для раздела подписок."""
    builder = InlineKeyboardBuilder()
    if has_configs:
        builder.row(
            InlineKeyboardButton(
                text="📥 Скачать конфиги", callback_data="download_configs",
            ),
        )
    builder.row(
        InlineKeyboardButton(text="🔄 Продлить", callback_data="buy_vpn"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_to_menu"),
    )
    return builder.as_markup()


def referral_keyboard(ref_link: str) -> InlineKeyboardMarkup:
    """Клавиатура реферальной системы."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📤 Отправить другу", switch_inline_query=ref_link),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_to_menu"),
    )
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Кнопка возврата в главное меню."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_to_menu"),
    )
    return builder.as_markup()
