"""
YooKassa Payment Service.

Создание платежей, обработка webhook уведомлений.
Документация: https://yookassa.ru/developers/api
SDK: https://github.com/yoomoney/yookassa-sdk-python
"""
from __future__ import annotations

import uuid
import logging
from decimal import Decimal

from yookassa import Configuration, Payment
from yookassa.domain.notification import (
    WebhookNotificationEventType,
    WebhookNotificationFactory,
)
from yookassa.domain.common import SecurityHelper
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import crud

logger = logging.getLogger(__name__)

# ── Инициализация SDK ──────────────────────────────────────────────────

Configuration.configure(settings.YOOKASSA_SHOP_ID, settings.YOOKASSA_SECRET_KEY)


class YooKassaService:
    """Сервис для работы с платежами ЮKassa."""

    @staticmethod
    async def create_payment(
        session: AsyncSession,
        user_id: int,
        plan: str,
        amount: Decimal,
        promo_code_id: int | None = None,
        description: str | None = None,
        customer_email: str | None = None,
    ) -> dict:
        """
        Создать платёж в ЮKassa и сохранить в БД.
        Включает данные чека для ФЗ-54 (самозанятый: tax_system_code=4, без НДС).

        Returns:
            dict с ключами:
                - payment_id: ID платежа в ЮKassa
                - confirmation_url: URL для перенаправления пользователя
                - db_payment: объект Payment из БД
        """
        plan_names = settings.plan_names
        if description is None:
            description = f"VPN подписка ({plan_names.get(plan, plan)})"

        # Создаём платёж в ЮKassa
        idempotency_key = str(uuid.uuid4())

        # Данные чека для ФЗ-54 (самозанятый)
        receipt: dict = {
            "items": [
                {
                    "description": description,
                    "quantity": "1.00",
                    "amount": {
                        "value": str(amount),
                        "currency": "RUB",
                    },
                    "vat_code": 1,  # Без НДС
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                },
            ],
            "tax_system_code": 4,  # Налог на профессиональный доход (самозанятый)
        }
        if customer_email:
            receipt["customer"] = {"email": customer_email}

        payment_data = {
            "amount": {
                "value": str(amount),
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": settings.YOOKASSA_RETURN_URL,
            },
            "capture": True,  # Автоматический capture (без двухстадийной оплаты)
            "description": description,
            "receipt": receipt,
            "metadata": {
                "user_id": str(user_id),
                "plan": plan,
                "promo_code_id": str(promo_code_id) if promo_code_id else "",
            },
        }

        try:
            yk_payment = Payment.create(payment_data, idempotency_key)
        except Exception as e:
            logger.error("Ошибка создания платежа в ЮKassa: %s", e)
            raise

        confirmation_url = yk_payment.confirmation.confirmation_url

        # Сохраняем платёж в БД
        db_payment = await crud.create_payment(
            session=session,
            user_id=user_id,
            yookassa_payment_id=yk_payment.id,
            amount=amount,
            plan=plan,
            confirmation_url=confirmation_url,
            promo_code_id=promo_code_id,
        )

        logger.info(
            "Платёж создан: yk_id=%s, user=%s, plan=%s, amount=%s",
            yk_payment.id, user_id, plan, amount,
        )

        return {
            "payment_id": yk_payment.id,
            "confirmation_url": confirmation_url,
            "db_payment": db_payment,
        }

    @staticmethod
    def verify_ip(ip: str) -> bool:
        """Проверить, что IP-адрес принадлежит ЮKassa."""
        return SecurityHelper().is_ip_trusted(ip)

    @staticmethod
    def parse_notification(event_json: dict) -> tuple[str, object]:
        """
        Распарсить webhook уведомление.

        Returns:
            (event_type, payment_object)
        """
        notification = WebhookNotificationFactory().create(event_json)
        return notification.event, notification.object

    @staticmethod
    async def process_succeeded(
        session: AsyncSession,
        payment_object: object,
    ) -> dict | None:
        """
        Обработать успешный платёж.
        Возвращает метаданные для активации подписки.
        """
        payment_id = payment_object.id
        metadata = payment_object.metadata or {}

        # Обновляем статус в БД
        db_payment = await crud.update_payment_status(
            session, payment_id, "succeeded",
        )

        if not db_payment:
            logger.warning("Платёж %s не найден в БД", payment_id)
            return None

        # Если промокод был использован — инкрементируем счётчик
        if db_payment.promo_code_id:
            await crud.increment_promo_usage(session, db_payment.promo_code_id)

        return {
            "user_id": db_payment.user_id,
            "plan": db_payment.plan,
            "payment_id": db_payment.id,
            "amount": db_payment.amount,
        }

    @staticmethod
    async def process_canceled(
        session: AsyncSession,
        payment_object: object,
    ) -> None:
        """Обработать отменённый платёж."""
        await crud.update_payment_status(
            session, payment_object.id, "canceled",
        )
        logger.info("Платёж %s отменён", payment_object.id)
