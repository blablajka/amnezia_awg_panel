"""
SQLAlchemy 2.0 модели для Amnezia VPN System.

Все модели используют Mapped-аннотации (modern SQLAlchemy 2.0 style).
Связи:
  User ─┬─< Subscription ─< UserServer >─ Server
        ├─< Payment
        └─< UserServer

  PromoCode >── Payment
  DailyStat — автономная таблица статистики
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── Enums ────────────────────────────────────────────────────────────────


class SubscriptionPlan(str, enum.Enum):
    """Доступные планы подписок."""
    ONE_MONTH = "1_month"
    THREE_MONTHS = "3_months"
    TWELVE_MONTHS = "12_months"


class SubscriptionStatus(str, enum.Enum):
    """Статусы подписки."""
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    """Статусы платежа."""
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


# ── Base ─────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""
    pass


# ── User ─────────────────────────────────────────────────────────────────


class User(Base):
    """
    Пользователь Telegram бота.
    Создаётся при первом /start.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    referrer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────
    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin",
    )
    payments: Mapped[list[Payment]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin",
    )
    user_servers: Mapped[list[UserServer]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin",
    )
    referrer: Mapped[Optional[User]] = relationship(
        "User", remote_side="User.id", backref="referrals", foreign_keys=[referrer_id],
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_id} @{self.username}>"


# ── Subscription ─────────────────────────────────────────────────────────


class Subscription(Base):
    """
    Подписка пользователя.
    Одна подписка = доступ ко всем серверам.
    """
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    plan: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default=SubscriptionStatus.ACTIVE.value, nullable=False,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────
    user: Mapped[User] = relationship(back_populates="subscriptions")
    payments: Mapped[list[Payment]] = relationship(
        back_populates="subscription", lazy="selectin",
    )
    user_servers: Mapped[list[UserServer]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan", lazy="selectin",
    )

    @property
    def is_active(self) -> bool:
        """Проверка, активна ли подписка по дате и статусу."""
        now = datetime.now(timezone.utc)
        return (
            self.status == SubscriptionStatus.ACTIVE.value
            and self.expires_at > now
        )

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} user={self.user_id} plan={self.plan} status={self.status}>"


# ── Server ───────────────────────────────────────────────────────────────


class Server(Base):
    """
    VPN сервер (Docker контейнер с AmneziaWG).
    Каждый сервер — отдельная страна.
    """
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # "Germany"
    host: Mapped[str] = mapped_column(String(255), nullable=False)  # SSH host/IP
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)  # SSH port
    awg_listen_port: Mapped[int] = mapped_column(Integer, default=39743, nullable=False)  # AWG UDP listen port
    ssh_user: Mapped[str] = mapped_column(String(255), default="root", nullable=False)
    ssh_key_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    ssh_password: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    docker_container: Mapped[str] = mapped_column(
        String(255), default="amnezia-wg-easy", nullable=False,
    )
    api_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # REST API URL
    api_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # REST API Token
    country_code: Mapped[str] = mapped_column(String(5), nullable=False)  # "DE", "NL", "LT"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    protocol: Mapped[Optional[str]] = mapped_column(
        String(50), default="awg", nullable=True,
    )  # awg, hysteria2, gost
    wg_config_path: Mapped[str] = mapped_column(
        String(512), default="/etc/amnezia/amneziawg/wg0.conf", nullable=False,
    )
    outbound_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # outbound WG config
    outbound_endpoint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # AWG 2.0 obfuscation params (JSON string): Jc, Jmin, Jmax, S1-S4, H1-H4, I1
    awg_params: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    awg_preset: Mapped[Optional[str]] = mapped_column(String(50), default="default", nullable=True)
    ipv6_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # IPv6 dual-stack in tunnel
    ipv6_subnet: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g. fddd:2c4:2c4:2c4::/64
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────
    user_servers: Mapped[list[UserServer]] = relationship(
        back_populates="server", cascade="all, delete-orphan", lazy="selectin",
    )

    @property
    def country_flag(self) -> str:
        """Эмодзи-флаг по коду страны."""
        flags = {"DE": "🇩🇪", "NL": "🇳🇱", "LT": "🇱🇹"}
        return flags.get(self.country_code, "🌍")

    def __repr__(self) -> str:
        return f"<Server id={self.id} {self.country_flag} {self.name} ({self.host})>"


# ── UserServer ───────────────────────────────────────────────────────────


class UserServer(Base):
    """
    Связь пользователя с конкретным VPN-сервером.
    Хранит конфигурацию клиента (WireGuard .conf).
    """
    __tablename__ = "user_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)  # Имя клиента в AWG
    config_data: Mapped[str] = mapped_column(Text, nullable=False, default="")  # Полный .conf
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    traffic_rx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Total bytes received
    traffic_tx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Total bytes sent
    traffic_limit_gb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # GB limit (None = unlimited)
    psk: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # PresharedKey for iOS/Shadowrocket
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )  # Per-client expiry override (falls back to subscription.expires_at if None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Constraints ──────────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("user_id", "server_id", "subscription_id", name="uq_user_server_sub"),
    )

    # ── Relationships ────────────────────────────────────────────────
    user: Mapped[User] = relationship(back_populates="user_servers")
    server: Mapped[Server] = relationship(back_populates="user_servers")
    subscription: Mapped[Subscription] = relationship(back_populates="user_servers")

    def __repr__(self) -> str:
        return f"<UserServer id={self.id} user={self.user_id} server={self.server_id}>"


# ── Payment ──────────────────────────────────────────────────────────────


class Payment(Base):
    """
    Платёж через ЮKassa.
    Создаётся при инициации оплаты, обновляется через webhook.
    """
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    subscription_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True,
    )
    yookassa_payment_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="RUB", nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default=PaymentStatus.PENDING.value, nullable=False,
    )
    plan: Mapped[str] = mapped_column(String(50), nullable=False)
    promo_code_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("promo_codes.id", ondelete="SET NULL"), nullable=True,
    )
    confirmation_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Relationships ────────────────────────────────────────────────
    user: Mapped[User] = relationship(back_populates="payments")
    subscription: Mapped[Optional[Subscription]] = relationship(back_populates="payments")
    promo_code: Mapped[Optional[PromoCode]] = relationship(back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment id={self.id} yk={self.yookassa_payment_id} status={self.status}>"


# ── PromoCode ────────────────────────────────────────────────────────────


class PromoCode(Base):
    """
    Промокод для скидки на подписку.
    Поддерживает процентную и фиксированную скидку.
    """
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0-100
    discount_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True,
    )  # Фиксированная скидка в рублях
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # None = безлимит
    current_uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    valid_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────
    payments: Mapped[list[Payment]] = relationship(
        back_populates="promo_code", lazy="selectin",
    )

    @property
    def is_valid(self) -> bool:
        """Проверка валидности промокода."""
        now = datetime.now(timezone.utc)
        if not self.is_active:
            return False
        if self.max_uses is not None and self.current_uses >= self.max_uses:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return now >= self.valid_from

    def calculate_discount(self, original_price: Decimal) -> Decimal:
        """Рассчитать скидку и вернуть итоговую цену."""
        if self.discount_amount:
            result = original_price - self.discount_amount
        elif self.discount_percent > 0:
            result = original_price * (100 - self.discount_percent) / 100
        else:
            result = original_price
        return max(result, Decimal("1.00"))  # Минимум 1 рубль

    def __repr__(self) -> str:
        return f"<PromoCode id={self.id} code={self.code} active={self.is_active}>"


# ── DailyStat ────────────────────────────────────────────────────────────


class DailyStat(Base):
    """
    Ежедневная агрегированная статистика.
    Заполняется через cron/scheduler.
    """
    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    new_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_subscriptions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    active_subscriptions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_payments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<DailyStat date={self.date} revenue={self.revenue}>"


# ── Bridge ─────────────────────────────────────────────────────────────────


class Bridge(Base):
    """
    Cascade tunnel between two servers.

    Architecture:
    Client → Russian VPS (server_from) → Foreign VPS (server_to) → Internet

    Russian VPS runs AWG client (awg1 interface).
    Foreign VPS runs AWG server for the tunnel.
    Traffic from awg0 (clients) not destined for .ru is routed to awg1 → Foreign → Internet.
    """
    __tablename__ = "bridges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_from_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    server_to_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    protocol: Mapped[str] = mapped_column(
        String(50), default="awg", nullable=False,
    )  # awg, hysteria2, gost
    tunnel_interface: Mapped[str] = mapped_column(
        String(50), default="awg1", nullable=False,
    )  # awg1, awg2, ... on Russian VPS
    local_subnet: Mapped[str] = mapped_column(
        String(50), default="10.10.0.1/24", nullable=False,
    )  # Tunnel subnet (server_from gets .1, server_to gets .2)
    listen_port: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
    )  # Auto-assigned if 0
    awg_params: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )  # AWG 2.0 obfuscation params for this tunnel (JSON)
    routing_mode: Mapped[str] = mapped_column(
        String(50), default="split", nullable=False,
    )  # "full" = all traffic through tunnel, "split" = only non-.ru
    config_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Tunnel config
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_installed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
    )  # True if server_to was auto-provisioned by panel
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────
    server_from: Mapped[Server] = relationship(
        "Server", foreign_keys=[server_from_id], lazy="selectin",
    )
    server_to: Mapped[Server] = relationship(
        "Server", foreign_keys=[server_to_id], lazy="selectin",
    )

    @property
    def tunnel_subnet_gateway(self) -> str:
        """Gateway IP for server_to side (e.g. 10.10.0.2)."""
        base = self.local_subnet.rsplit(".", 2)[0]
        return f"{base}.2"

    def __repr__(self) -> str:
        return f"<Bridge id={self.id} {self.server_from_id}->{self.server_to_id} [{self.protocol}] {self.tunnel_interface}>"


