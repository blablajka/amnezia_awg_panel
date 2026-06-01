"""
Конфигурация приложения Amnezia VPN System.
Все чувствительные данные загружаются из .env файла.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Главный класс настроек приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram Bot ─────────────────────────────────────────────────────
    BOT_TOKEN: str
    ADMIN_IDS: list[int] = []  # Telegram ID администраторов (через запятую в .env)

    # ── YooKassa ─────────────────────────────────────────────────────────
    YOOKASSA_SHOP_ID: str
    YOOKASSA_SECRET_KEY: str
    YOOKASSA_RETURN_URL: str = "https://t.me/your_bot"  # URL возврата после оплаты

    # ── Redis ──────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── Database ─────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./vpn_system.db"

    # ── Web Admin Panel ──────────────────────────────────────────────────
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000
    SECRET_KEY: str = "change-me-in-production-please"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"  # В продакшне — хэш bcrypt

    # ── Webhook (для приёма уведомлений от ЮKassa) ───────────────────────
    WEBHOOK_BASE_URL: str = "https://yourdomain.com"
    WEBHOOK_PATH: str = "/api/yookassa/webhook"

    # ── Реферальная система ──────────────────────────────────────────────
    REFERRAL_BONUS_DAYS: int = 3  # Дней подписки за приглашённого друга

    # ── Поддержка ─────────────────────────────────────────────────────────
    SUPPORT_USERNAME: str = "@your_support_username"

    # ── Цены подписок (в рублях) ─────────────────────────────────────────
    PRICE_1_MONTH: int = 290
    PRICE_3_MONTHS: int = 690
    PRICE_12_MONTHS: int = 2490

    @property
    def webhook_url(self) -> str:
        """Полный URL webhook для ЮKassa."""
        return f"{self.WEBHOOK_BASE_URL.rstrip('/')}{self.WEBHOOK_PATH}"

    @property
    def prices(self) -> dict[str, int]:
        """Словарь цен по планам."""
        return {
            "1_month": self.PRICE_1_MONTH,
            "3_months": self.PRICE_3_MONTHS,
            "12_months": self.PRICE_12_MONTHS,
        }

    @property
    def plan_names(self) -> dict[str, str]:
        """Человекочитаемые названия планов."""
        return {
            "7_days": "7 дней (Пробный)",
            "1_month": "1 месяц",
            "3_months": "3 месяца",
            "12_months": "12 месяцев",
        }

    @property
    def plan_days(self) -> dict[str, int]:
        """Количество дней по планам."""
        return {
            "7_days": 7,
            "1_month": 30,
            "3_months": 90,
            "12_months": 365,
        }


# Глобальный singleton настроек
settings = Settings()  # type: ignore[call-arg]
