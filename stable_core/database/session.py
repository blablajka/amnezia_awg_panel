"""
Async SQLAlchemy session factory.
Поддерживает SQLite (aiosqlite) и PostgreSQL (asyncpg).
"""
from __future__ import annotations
from collections.abc import AsyncGenerator
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

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
