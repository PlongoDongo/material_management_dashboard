"""
Integration tests against a REAL Postgres.

Why this file exists: db/models.py describes a table that belongs to another
team, and nothing without a database can say whether the class still matches it
-- column names, types, NOT NULL. A fake would only repeat our own assumptions,
and those were wrong twice already (UUID instead of text, and the two `sync_*`
columns that have no DDL default).

    Without a database:  every test here is SKIPPED.
    With a database:

        export SQL_HOST=localhost SQL_USERNAME=postgres SQL_PASSWORD=postgres
        export SQL_DATABASE=postgres
        pytest tests/test_integration_postgres.py -v

The `changelog` table belongs to another team. The fixture creates it only when
it is missing and NEVER drops it; each test deletes the rows it wrote.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select

from core.config import Settings
from db.models import Changelog
from db.sources import Sources
from db.sql import create_engine, create_sessionmaker, dispose_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("SQL_HOST"),
    reason="SQL_HOST is not set -- integration tests skipped.",
)

# The DDL SQLModel generates for db/models.py::Changelog (checked with
# sqlalchemy.schema.CreateTable): the Python defaults on `user_id`, `session_id`,
# `sync_status` and `sync_attempts` do NOT appear here, only `created_at` has a
# real server default. Used only when the table does not exist yet.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS changelog (
    changelog_id  uuid         NOT NULL PRIMARY KEY,
    user_id       varchar(255) NOT NULL,
    change_type   varchar(100) NOT NULL,
    payload       json         NOT NULL,
    session_id    varchar(255) NOT NULL,
    created_at    timestamp    NOT NULL DEFAULT now(),
    sync_status   varchar(50)  NOT NULL,
    sync_error    varchar,
    sync_attempts integer      NOT NULL,
    synced_at     timestamp
)
"""

RELATIONSHIP = {
    "material_rep_1_id": "11111111-1111-1111-1111-111111111111",
    "material_rep_2_id": "22222222-2222-2222-2222-222222222222",
    "relationship_type": "IS_SAME",
}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    """ONE engine for the whole session -- the same rule as in the server."""
    settings = Settings()
    sql_engine = create_engine(settings.sql_url, ssl=settings.sql_ssl)
    assert sql_engine is not None, "SQL_HOST is set but the DSN is incomplete."
    try:
        async with sql_engine.begin() as connection:
            await connection.execute(text(CREATE_TABLE))
        yield sql_engine
    finally:
        await dispose_engine(sql_engine)


@pytest_asyncio.fixture(loop_scope="session")
async def sources(engine: AsyncEngine) -> AsyncIterator[Sources]:
    """One Sources object per test -- short-lived, as in a request."""
    async with AsyncExitStack() as stack:
        yield Sources(stack=stack, settings=Settings(), neo4j_driver=None,
                      sql_sessionmaker=create_sessionmaker(engine))


async def _row(engine: AsyncEngine, changelog_id: object) -> dict:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT * FROM changelog WHERE changelog_id = :id"), {"id": changelog_id}
        )
        return dict(result.mappings().one())


async def _exists(engine: AsyncEngine, changelog_id: object) -> bool:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT 1 FROM changelog WHERE changelog_id = :id"), {"id": changelog_id}
        )
        return result.first() is not None


async def _delete(engine: AsyncEngine, changelog_id: object) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM changelog WHERE changelog_id = :id"), {"id": changelog_id}
        )


async def test_the_table_class_matches_the_real_table(
    sources: Sources, engine: AsyncEngine
) -> None:
    """The whole point of this file, checked against a database instead of
    against our own fake: the class writes a row the table accepts, its Python
    defaults end up in that row, and `refresh()` reads the server-generated
    `created_at` back.
    """
    entry = Changelog(
        user_id="a.schmidt",
        change_type="MATERIALS_RELATIONSHIP_CREATED",
        payload=RELATIONSHIP,
        session_id="unknown",
    )
    try:
        await sources.add(entry)
        await sources.commit()

        assert entry.created_at is not None, "refresh() did not read the server default back"

        row = await _row(engine, entry.changelog_id)
        assert row["user_id"] == "a.schmidt"
        assert row["change_type"] == "MATERIALS_RELATIONSHIP_CREATED"
        assert row["session_id"] == "unknown"
        assert row["sync_status"] == "pending"     # from the class, not from the route
        assert row["sync_attempts"] == 0
        assert row["synced_at"] is None

        # The driver may hand a json column back as text or as a dict.
        payload = row["payload"]
        assert (json.loads(payload) if isinstance(payload, str) else payload) == RELATIONSHIP
    finally:
        await _delete(engine, entry.changelog_id)


async def test_reading_changing_and_deleting_through_the_class(
    sources: Sources, engine: AsyncEngine
) -> None:
    """The round trip an endpoint makes -- and the part no fake can show: the
    change is written although no UPDATE appears anywhere in the code.
    """
    entry = Changelog(change_type="MATERIALS_RELATIONSHIP_CREATED", payload=RELATIONSHIP)
    await sources.add(entry)
    await sources.commit()
    changelog_id = entry.changelog_id

    try:
        [found] = await sources.exec(
            select(Changelog).where(Changelog.changelog_id == changelog_id)
        )
        assert found.sync_status == "pending"

        found.sync_status = "done"
        await sources.commit()
        assert (await _row(engine, changelog_id))["sync_status"] == "done"

        by_key = await sources.get(Changelog, changelog_id)
        assert by_key is not None and by_key.change_type == "MATERIALS_RELATIONSHIP_CREATED"

        await sources.delete(by_key)
        await sources.commit()
        assert not await _exists(engine, changelog_id)
    finally:
        await _delete(engine, changelog_id)
