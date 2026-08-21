"""
Postgres: Engine-Lebenszyklus (SQLAlchemy 2.x, async).

Dieselbe Regel wie bei Neo4j, nur anders benannt:

    ENGINE   ~ Treiber   -> einer pro Prozess (haelt den Pool)
    SESSION  ~ Session   -> eine pro Request

`expire_on_commit=False`, weil wir nach dem Commit noch aus den Objekten lesen
wollen, ohne dass SQLAlchemy nachlaedt (die Session ist dann evtl. schon zu).

Der DSN MUSS den Async-Treiber nennen: `postgresql+asyncpg://...`.
Ein blosses `postgresql://` waehlt psycopg2 (synchron) und blockiert den
Event-Loop.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

SessionMaker = async_sessionmaker[AsyncSession]


def create_engine(dsn: str | None) -> AsyncEngine | None:
    if not dsn:
        log.warning("POSTGRES_DSN nicht gesetzt -- SQL inaktiv.")
        return None
    if "+asyncpg" not in dsn and "+psycopg" not in dsn:
        log.warning("POSTGRES_DSN ohne Async-Treiber (%s) -- erwartet 'postgresql+asyncpg://'.",
                    dsn.split("://")[0])
    engine = create_async_engine(
        dsn,
        pool_size=5,          # gleichzeitige Verbindungen pro Prozess
        max_overflow=5,       # kurzfristige Spitzen
        pool_pre_ping=True,   # tote Verbindungen (Firewall-Timeout) erkennen
    )
    log.info("SQL-Engine erzeugt.")
    return engine


def create_sessionmaker(engine: AsyncEngine | None) -> SessionMaker | None:
    if engine is None:
        return None
    return async_sessionmaker(engine, expire_on_commit=False)


async def dispose_engine(engine: AsyncEngine | None) -> None:
    if engine is not None:
        await engine.dispose()
        log.info("SQL-Engine geschlossen.")
