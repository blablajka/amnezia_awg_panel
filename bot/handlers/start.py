"""
Handler: /start — регистрация пользователя и главное меню.
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import crud
from bot.keyboards.inline import main_menu_keyboard, referral_keyboard, back_to_menu_keyboard

logger = logging.getLogger(__name__)
router = Router(name="start")


# ── Helpers ────────────────────────────────────────────────────────────────


async def _award_referral_bonus(session: AsyncSession, referrer_id: int, invited_name: str) -> None:
    """Начислить бонусные дни рефереру за приглашённого друга."""
    from datetime import timedelta
    from services.subscription_service import SubscriptionService

    bonus_days = settings.REFERRAL_BONUS_DAYS
    sub = await crud.get_active_subscription(session, referrer_id)
    if sub:
        sub.expires_at = sub.expires_at + timedelta(days=bonus_days)
        logger.info("Referral bonus: +%d days to user %s", bonus_days, referrer_id)
    # Если нет активной подписки — бонус не начисляется, но реферал сохранён


# ── Текст приветствия ────────────────────────────────────────────────────

WELCOME_TEXT = """
Добро пожаловать в Blue Orb VPN 💙

- Самый быстрый протокол AmneziaWG (быстрее чем подписки в Happ, v2raytun)
- Полная приватность
- Ограниченное число пользователей
- Без рекламы 

Опробуйте 7 дней бесплатно
"""

HELP_TEXT = """
ℹ️ <b>Справка</b>

<b>Как пользоваться:</b>
1. Нажмите «🛒 Купить VPN» и выберите тариф
2. Оплатите через ЮKassa (карта, СБП, кошелёк)
3. Получите конфигурации для всех 3 серверов
4. Установите <a href="https://amnezia.org">Amnezia VPN</a> и импортируйте конфиг

<b>Тарифы:</b>
📅 1 месяц — 290₽
📅 3 месяца — 690₽ (экономия 21%)
🟢 12 месяцев — 2490₽ (выгода 40%)

<b>Поддержка:</b> @your_support_username
"""


# ── Handlers ─────────────────────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    """Обработка команды /start — регистрация + главное меню."""
    await state.clear()

    # Проверяем, новый ли это пользователь
    existing_user = await crud.get_user_by_telegram_id(session, message.from_user.id)
    is_new_user = existing_user is None

    # Обработка реферального deep-link: /start ref_<telegram_id>
    referrer_id: int | None = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_tg_id = int(args[1].split("_")[1])
            # Не даём рефералиться самому себе
            if ref_tg_id != message.from_user.id:
                referrer = await crud.get_user_by_telegram_id(session, ref_tg_id)
                if referrer:
                    referrer_id = referrer.id
        except (ValueError, IndexError):
            pass

    # Регистрируем / обновляем пользователя
    user = await crud.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referrer_id=referrer_id if is_new_user and referrer_id else None,
    )
    logger.info("User started: %s (@%s), is_new: %s, referrer: %s", user.telegram_id, user.username, is_new_user, referrer_id)

    # Если новый пользователь пришёл по реферальной ссылке — начисляем бонус рефереру
    if is_new_user and referrer_id:
        await _award_referral_bonus(session, referrer_id, message.from_user.full_name)

    if is_new_user:
        # Отправляем стикер приветствия только новым пользователям
        try:
            await message.answer_sticker(
                sticker="CAACAgIAAxkBAAERMR5qAQmk-DsSgUJbuumJXTO6h6Qs_AACDQADwDZPE6T54fTUeI1TOwQ"
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить стикер: {e}")

    # Отправляем картинку с текстом в качестве подписи ВСЕМ пользователям
    import os
    from aiogram.types import FSInputFile

    photo_path = os.path.join("pictures", "welcome.png")
    
    global WELCOME_PHOTO_ID
    
    if 'WELCOME_PHOTO_ID' not in globals():
        WELCOME_PHOTO_ID = None

    has_subscription = await crud.get_active_subscription(session, message.from_user.id) is not None

    if WELCOME_PHOTO_ID:
        # Если картинка уже была загружена на сервера Telegram, отправляем по её быстрому ID
        try:
            await message.answer_photo(
                photo=WELCOME_PHOTO_ID,
                caption=WELCOME_TEXT,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(has_subscription),
            )
            return
        except Exception:
            WELCOME_PHOTO_ID = None # Сброс, если ID стал недействительным

    if os.path.exists(photo_path):
        try:
            photo = FSInputFile(photo_path)
            sent_message = await message.answer_photo(
                photo=photo,
                caption=WELCOME_TEXT,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(has_subscription),
            )
            # Сохраняем file_id для мгновенной отправки в будущем
            if sent_message.photo:
                WELCOME_PHOTO_ID = sent_message.photo[-1].file_id
            return
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
    
    # Фолбэк (резервный вариант): если картинки нет, отправляем просто текст
    await message.answer(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(has_subscription),
    )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    """Возврат в главное меню."""
    await state.clear()
    has_subscription = await crud.get_active_subscription(session, callback.from_user.id) is not None
    try:
        await callback.message.edit_text(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(has_subscription),
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(has_subscription),
        )
    await callback.answer()


@router.callback_query(F.data == "connect_instructions")
async def connect_instructions(callback: CallbackQuery) -> None:
    """Выбор платформы для подключения."""
    from bot.keyboards.inline import platforms_keyboard
    
    text = (
        "⚡ <b>Как подключиться</b>\n\n"
        "Выберите операционную систему вашего устройства, чтобы получить ссылки на скачивание и инструкции:"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=platforms_keyboard(),
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=platforms_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("platform_"))
async def platform_selected(callback: CallbackQuery) -> None:
    """Инструкции по скачиванию для выбранной платформы."""
    from bot.keyboards.inline import back_to_menu_keyboard
    platform = callback.data.split("_")[1]
    
    if platform == "windows" or platform == "linux":
        text = (
            f"💻 <b>Инструкция для {platform.capitalize()}</b>\n\n"
            "Для вашей ОС мы рекомендуем использовать клиент <b>Wiresock</b>, так как он обеспечивает максимальную скорость и стабильность с нашим протоколом.\n\n"
            "📥 <b>Скачать Wiresock:</b>\n"
            "<a href='https://www.wiresock.net/wiresock-secure-connect/download'>Скачать с официального сайта</a>\n\n"
            "<b>Как настроить:</b>\n"
            "1. Установите приложение\n"
            "2. Перейдите в 'Моя подписка' и скачайте ваш <code>.conf</code> файл конфигурации\n"
            "3. Импортируйте этот файл в Wiresock и нажмите Подключиться!"
        )
    elif platform == "android":
        text = (
            "🤖 <b>Инструкция для Android</b>\n\n"
            "Для Android лучше всего подходит официальное приложение <b>AmneziaVPN</b>.\n\n"
            "📥 <b>Скачать AmneziaVPN:</b>\n"
            "• <a href='https://play.google.com/store/apps/details?id=org.amnezia.vpn'>Google Play Store</a>\n"
            "• <a href='https://amnezia.org/downloads'>APK с сайта Amnezia</a>\n\n"
            "<b>Как настроить:</b>\n"
            "1. Установите приложение\n"
            "2. Перейдите в 'Моя подписка' и скачайте ваш <code>.conf</code> файл конфигурации\n"
            "3. Откройте файл через приложение AmneziaVPN или скопируйте его содержимое"
        )
    elif platform == "ios":
        text = (
            "🍏 <b>Инструкция для iOS (iPhone/iPad)</b>\n\n"
            "В связи с блокировками, оригинальное приложение AmneziaVPN недоступно в российском App Store. "
            "Используйте официальные альтернативы от разработчиков Amnezia:\n\n"
            "📥 <b>Скачать:</b>\n"
            "• <a href='https://apps.apple.com/us/app/amneziawg/id6478942365'>AmneziaWG</a> (рекомендуется)\n"
            "• <a href='https://apps.apple.com/us/app/defaultvpn/id6744725017'>DefaultVPN</a>\n\n"
            "<b>Как настроить:</b>\n"
            "1. Установите любое из этих приложений\n"
            "2. Перейдите в 'Моя подписка' и скачайте ваш <code>.conf</code> файл конфигурации\n"
            "3. Откройте приложение, нажмите 'Добавить туннель' и выберите скачанный файл"
        )
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
            disable_web_page_preview=True,
        )
    await callback.answer()

# ═══════════════════════════════════════════════════════════════════
# Пригласить друга — реферальная система
# ═══════════════════════════════════════════════════════════════════


@router.callback_query(F.data == "invite_friend")
async def invite_friend_handler(callback: CallbackQuery, session: AsyncSession) -> None:
    """Показать реферальную ссылку и статистику."""
    user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    bot_username = (await callback.bot.me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{callback.from_user.id}"

    referrals_count = await crud.count_referrals(session, user.id)
    bonus_days = referrals_count * settings.REFERRAL_BONUS_DAYS

    text = (
        "🤝 <b>Пригласи друга — получи бонус!</b>\n\n"
        f"За каждого друга, который зарегистрируется по вашей ссылке, "
        f"вы получите <b>+{settings.REFERRAL_BONUS_DAYS} дня</b> к подписке.\n\n"
        f"🔗 <b>Ваша ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"👥 Приглашено: <b>{referrals_count}</b>\n"
        f"🎁 Бонусных дней: <b>{bonus_days}</b>\n"
    )

    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=referral_keyboard(ref_link),
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            text, parse_mode="HTML",
            reply_markup=referral_keyboard(ref_link),
            disable_web_page_preview=True,
        )
    await callback.answer()


# ═══════════════════════════════════════════════════════════════════
# Поддержка — FAQ + контакт
# ═══════════════════════════════════════════════════════════════════


FAQ_TEXT = """
🆘 <b>Часто задаваемые вопросы</b>

<b>1. Как подключиться на Windows / Linux?</b>
Мы рекомендуем <a href='https://www.wiresock.net/wiresock-secure-connect/download'>Wiresock</a> — он обеспечивает максимальную скорость.

<b>2. Как подключиться на Android?</b>
Установите <a href='https://play.google.com/store/apps/details?id=org.amnezia.vpn'>AmneziaVPN</a> и импортируйте .conf файл.

<b>3. Как подключиться на iOS?</b>
Используйте <a href='https://apps.apple.com/us/app/amneziawg/id6478942365'>AmneziaWG</a> или <a href='https://apps.apple.com/us/app/defaultvpn/id6744725017'>DefaultVPN</a>.

<b>4. Я оплатил, но не пришли конфиги</b>
Нажмите «Я оплатил» в боте. Если не помогло — напишите в поддержку.

<b>5. Как продлить подписку?</b>
Нажмите «Моя подписка» → «Продлить».

<b>6. Сколько устройств можно подключить?</b>
До 5 устройств одновременно.

<b>7. Что делать, если VPN не работает?</b>
Попробуйте переподключиться или переключиться на другой сервер (у вас есть конфиги для DE, NL, LT).
"""


@router.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery) -> None:
    """Показать FAQ и контакт поддержки."""
    text = FAQ_TEXT + f"\n━━━━━━━━━━━━━━━━━━━━━━\n📩 <b>Связь с поддержкой:</b> {settings.SUPPORT_USERNAME}"

    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            text, parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
            disable_web_page_preview=True,
        )
    await callback.answer()


# ═══════════════════════════════════════════════════════════════════
# Правила
# ═══════════════════════════════════════════════════════════════════


RULES_TEXT = """
📜 <b>Правила использования</b>

<b>1. Общие положения</b>
Сервис предоставляет доступ к VPN на базе протокола AmneziaWG. Используя сервис, вы соглашаетесь с данными правилами.

<b>2. Запрещено</b>
• Распространение вредоносного ПО
• DDoS-атаки, сканирование портов
• Спам и фишинг
• Нарушение законодательства РФ и страны сервера
• Продажа и перепродажа доступа

<b>3. Ответственность</b>
Мы не несём ответственности за противоправные действия пользователей. При выявлении нарушений доступ блокируется без возврата средств.

<b>4. Возвраты</b>
Возврат возможен в течение 24 часов после оплаты, если конфигурации не были скачаны.

<b>5. Изменение условий</b>
Мы оставляем за собой право изменять правила. Пользователи будут уведомлены через бота.
"""


@router.callback_query(F.data == "rules")
async def rules_handler(callback: CallbackQuery) -> None:
    """Показать правила использования."""
    try:
        await callback.message.edit_text(
            RULES_TEXT, parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            RULES_TEXT, parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
        )
    await callback.answer()

# ═══════════════════════════════════════════════════════════════════
# /help — справка
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Показать справку."""
    await message.answer(HELP_TEXT, parse_mode="HTML", disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════
# copy_ref — копирование реферальной ссылки
# ═══════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("copy_ref:"))
async def copy_ref_handler(callback: CallbackQuery) -> None:
    """Скопировать реферальную ссылку."""
    ref_link = callback.data.split(":", 1)[1]
    await callback.answer(
        "✅ Ссылка скопирована! Отправьте её другу.\n\n"
        f"Ссылка: {ref_link}",
        show_alert=True,
    )


@router.callback_query(F.data == "activate_trial")
async def activate_trial_handler(callback: CallbackQuery, session: AsyncSession) -> None:
    """Активация 7-дневного пробного периода."""
    user_id = callback.from_user.id
    
    # Проверяем, есть ли уже подписка у пользователя
    existing = await crud.get_active_subscription(session, user_id)
    if existing:
        await callback.answer("❌ У вас уже есть подписка!", show_alert=True)
        return

    # Отправляем сообщение о загрузке
    try:
        await callback.message.edit_text("⏳ <i>Подготавливаем ваш сервер... Это займет несколько секунд.</i>", parse_mode="HTML")
    except Exception:
        await callback.message.delete()
        await callback.message.answer("⏳ <i>Подготавливаем ваш сервер... Это займет несколько секунд.</i>", parse_mode="HTML")

    try:
        # Создаем подписку
        from services.subscription_service import SubscriptionService
        sub = await SubscriptionService.activate_subscription(session, user_id, "7_days")
        
        # Создаем конфиги на серверах
        results = await SubscriptionService.provision_all_servers(session, user_id, sub.id)
        
        if not any(r.get("success") for r in results):
            text = "❌ Ошибка при создании конфигурации на серверах. Обратитесь в поддержку."
        else:
            text = (
                "✅ <b>Пробный период активирован!</b>\n\n"
                "Вам начислено 7 дней доступа к VPN. Перейдите в раздел <b>Моя подписка</b>, чтобы получить настройки подключения."
            )
        
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(has_subscription=True),
            )
        except Exception:
            await callback.message.delete()
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(has_subscription=True),
            )

    except Exception as e:
        logger.error(f"Error activating trial for user {user_id}: {e}")
        try:
            await callback.message.edit_text("❌ Произошла непредвиденная ошибка.")
        except Exception:
            await callback.message.answer("❌ Произошла непредвиденная ошибка.")

    await callback.answer()
