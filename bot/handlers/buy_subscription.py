"""
Handler: Выбор подписки — мгновенная выдача (демо-режим без оплаты).

Этапы:
1. Выбор тарифа (1/3/12 мес)
2. Мгновенная активация и выдача конфига
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from bot.states.subscription_states import BuySubscription
from bot.keyboards.inline import plan_selection_keyboard
from database import crud
from services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)
router = Router(name="buy_subscription")


@router.callback_query(F.data == "buy_vpn")
async def start_buy(callback: CallbackQuery, state: FSMContext) -> None:
    """Начало покупки — показать тарифы."""
    await state.clear()
    await state.set_state(BuySubscription.select_plan)

    text = (
        "🛒 <b>Выберите тарифный план</b>\n\n"
        "Ваша подписка обеспечит доступ ко всем ресурсам через мост.\n"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=plan_selection_keyboard(),
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            text, parse_mode="HTML",
            reply_markup=plan_selection_keyboard(),
        )
    await callback.answer()


@router.callback_query(
    BuySubscription.select_plan,
    F.data.startswith("plan:"),
)
async def plan_selected(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Тариф выбран — мгновенно активировать подписку и выдать конфиг."""
    plan = callback.data.split(":")[1]  # "1_month", "3_months", "12_months"
    plan_name = settings.plan_names.get(plan, plan)

    await callback.message.edit_text("⏳ Активируем подписку и создаём конфигурации...")

    user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден. Введите /start")
        return

    try:
        # Активируем подписку
        sub = await SubscriptionService.activate_subscription(
            session=session,
            user_id=user.id,
            plan=plan,
        )

        # Создаём клиентов на всех серверах (обычно 1 сервер — RU, который маршрутизирует дальше)
        configs = await SubscriptionService.provision_all_servers(
            session=session,
            user_id=user.id,
            subscription_id=sub.id,
        )

        await session.commit()

        success_count = sum(1 for c in configs if c["success"])
        total_count = len(configs)

        text = (
            f"🎉 <b>Подписка успешно активирована!</b>\n\n"
            f"📅 План: {plan_name}\n"
            f"📆 Действует до: {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
        )

        if success_count > 0:
            text += "📥 <b>Ваша конфигурация AmneziaWG:</b>\n"
        
        await callback.message.edit_text(text, parse_mode="HTML")

        # Отправляем каждый конфиг
        for cfg in configs:
            if not cfg["success"]:
                await callback.message.answer(
                    f"⚠️ Ошибка на {cfg['server'].country_flag} {cfg['server'].name}: "
                    f"{cfg.get('error', 'Неизвестная ошибка')}",
                )
                continue

            server = cfg["server"]
            config_data = cfg["config"]
            filename = f"awg_{server.country_code.lower()}_{server.name.lower()}.conf"

            file = BufferedInputFile(
                config_data.encode("utf-8"),
                filename=filename,
            )
            await callback.message.answer_document(
                document=file,
                caption=(
                    f"{server.country_flag} <b>{server.name}</b>\n"
                    f"Импортируйте этот файл в приложение AmneziaWG или WireGuard"
                ),
                parse_mode="HTML",
            )

        await state.clear()
        await callback.message.answer(
            "✅ <b>Готово!</b>\n\nВключите VPN. "
            "Российские сайты будут открываться напрямую, а остальные — через защищенный туннель!\n\n"
            "По вопросам — /start",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error("Ошибка при выдаче конфига: %s", e)
        await session.rollback()
        await callback.message.edit_text("❌ Ошибка при генерации конфигурации. Обратитесь в поддержку.")

