"""
Postgres: engine lifecycle (SQLAlchemy 2.x, async).

Same rule as for Neo4j, only the names differ:

    ENGINE  ~ driver   -> one per process (owns the pool)
    SESSION ~ session  -> one per request

`expire_on_commit=False`, because we still want to read from objects after a
commit without SQLAlchemy reloading them (the session may already be closed).

The DSN MUST name the async driver: `postgresql+asyncpg://...`. A plain
`postgresql://` selects psycopg2 (synchronous) and blocks the event loop.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

log = logging.getLogger(__name__)

SessionMaker = async_sessionmaker[AsyncSession]


def create_engine(dsn: str | None) -> AsyncEngine | None:
    if not dsn:
        log.warning("POSTGRES_DSN is not set -- SQL inactive.")
        return None
    if "+asyncpg" not in dsn and "+psycopg" not in dsn:
        log.warning("POSTGRES_DSN without an async driver (%s) -- expected 'postgresql+asyncpg://'.",
                    dsn.split("://")[0])
    engine = create_async_engine(
        dsn,
        pool_size=5,          # concurrent connections per process
        max_overflow=5,       # short-lived spikes
        pool_pre_ping=True,   # detect dead connections (firewall timeouts)
    )
    log.info("SQL engine created.")
    return engine


def create_sessionmaker(engine: AsyncEngine | None) -> SessionMaker | None:
    if engine is None:
        return None
    return async_sessionmaker(engine, expire_on_commit=False)


async def dispose_engine(engine: AsyncEngine | None) -> None:
    if engine is not None:
        await engine.dispose()
        log.info("SQL engine disposed.")
