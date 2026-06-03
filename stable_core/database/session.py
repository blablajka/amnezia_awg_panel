"""
Async SQLAlchemy session factory.
Supports SQLite (aiosqlite) and PostgreSQL (asyncpg).
Auto-migrates missing columns on startup.
"""
from __future__ import annotations
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)

from config import settings
from database.models import Base

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL, echo=False,
    **({"pool_size": 10, "max_overflow": 20} if not _is_sqlite else {}),
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _migrate_sqlite(conn) -> None:
    """Auto-add missing columns to SQLite tables on startup."""
    import logging
    log = logging.getLogger("panel.migrate")

    model_columns: dict[str, dict[str, tuple[str, str]]] = {
        "servers": {
            "awg_subnet": ("VARCHAR(50)", "'10.9.9.1/24'"),
            "awg_listen_port": ("INTEGER", "39743"),
            "awg_params": ("TEXT", "NULL"),
            "awg_preset": ("VARCHAR(50)", "'default'"),
            "ipv6_enabled": ("BOOLEAN", "1"),
            "ipv6_subnet": ("VARCHAR(50)", "NULL"),
            "protocol": ("VARCHAR(50)", "'awg'"),
            "api_url": ("VARCHAR(512)", "NULL"),
            "api_token": ("VARCHAR(512)", "NULL"),
        },
        "user_servers": {
            "traffic_rx": ("BIGINT", "0"),
            "traffic_tx": ("BIGINT", "0"),
            "traffic_limit_gb": ("INTEGER", "NULL"),
            "psk": ("VARCHAR(255)", "NULL"),
            "expires_at": ("DATETIME", "NULL"),
        },
        "bridges": {
            "awg_params": ("TEXT", "NULL"),
            "routing_mode": ("VARCHAR(50)", "'split'"),
            "auto_installed": ("BOOLEAN", "0"),
        },
        "subscriptions": {
            "plan": ("VARCHAR(50)", "'1_month'"),
        },
    }

    for table, columns in model_columns.items():
        result = await conn.execute(text("PRAGMA table_info(%s)" % table))
        existing = {row[1] for row in result.fetchall()}

        for col_name, (col_type, default_val) in columns.items():
            if col_name not in existing:
                sql = "ALTER TABLE %s ADD COLUMN %s %s DEFAULT %s" % (
                    table, col_name, col_type, default_val,
                )
                try:
                    await conn.execute(text(sql))
                    log.info("Migrated: %s.%s %s", table, col_name, col_type)
                except Exception as e:
                    log.warning("Migration skip %s.%s: %s", table, col_name, e)


async def init_db() -> None:
    """Create tables and auto-migrate missing columns."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if _is_sqlite:
            await _migrate_sqlite(conn)
        await conn.commit()
