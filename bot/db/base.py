"""
Async SQLAlchemy engine + session plumbing.

The engine is created once at startup (``init_db``). Route handlers and service
modules acquire sessions via ``session_scope()``; they never touch the engine
directly. For v1 we use ``create_all`` on startup rather than Alembic — the
schema is small and additive, and there is no existing data to migrate. If the
schema starts changing in incompatible ways, swap this for Alembic.
"""

import logging

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

import config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine = None
_sessionmaker: "async_sessionmaker[AsyncSession] | None" = None


def get_sessionmaker() -> "async_sessionmaker[AsyncSession] | None":
    """Return the session factory, or None if the DB never initialized."""
    return _sessionmaker


async def init_db() -> None:
    """Create the engine, register models, and ensure tables exist. Raises if
    ``DATABASE_URL`` is unset or the database is unreachable — the caller in
    ``bot.py`` treats that as non-fatal so the Discord bot still runs."""
    global _engine, _sessionmaker

    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set — cannot initialize the database")

    _engine = create_async_engine(config.DATABASE_URL, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    # Import models so they register on Base.metadata before create_all.
    import db.models  # noqa: F401

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database ready — tables ensured")
