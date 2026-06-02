"""
Promo Code Service — валидация и применение промокодов.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import PromoCode

logger = logging.getLogger(__name__)


class PromoService:
    """Сервис работы с промокодами."""

    @staticmethod
    async def validate_promo(
        session: AsyncSession, code: str,
    ) -> tuple[bool, str, PromoCode | None]:
        """
        Проверить валидность промокода.
        Returns: (is_valid, message, promo_object)
        """
        promo = await crud.get_promo_by_code(session, code)

        if promo is None:
            return False, "❌ Промокод не найден", None

        if not promo.is_active:
            return False, "❌ Промокод деактивирован", None

        now = datetime.now(timezone.utc)

        if promo.valid_until and now > promo.valid_until:
            return False, "❌ Срок действия промокода истёк", None

        if promo.max_uses is not None and promo.current_uses >= promo.max_uses:
            return False, "❌ Промокод исчерпан", None

        # Формируем описание скидки
        if promo.discount_amount:
            desc = f"Скидка {promo.discount_amount}₽"
        else:
            desc = f"Скидка {promo.discount_percent}%"

        return True, f"✅ Промокод принят! {desc}", promo

    @staticmethod
    def apply_discount(promo: PromoCode, original_price: Decimal) -> Decimal:
        """Применить скидку и вернуть итоговую цену."""
        return promo.calculate_discount(original_price)

    @staticmethod
    async def create_promo(
        session: AsyncSession,
        code: str,
        discount_percent: int = 0,
        discount_amount: Decimal | None = None,
        max_uses: int | None = None,
        valid_until: datetime | None = None,
    ) -> PromoCode:
        """Создать новый промокод."""
        promo = await crud.create_promo_code(
            session=session,
            code=code,
            discount_percent=discount_percent,
            discount_amount=discount_amount,
            max_uses=max_uses,
            valid_until=valid_until,
        )
        logger.info("Промокод создан: %s (-%s%%)", code, discount_percent)
        return promo
