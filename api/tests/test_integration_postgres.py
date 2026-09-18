"""
Integration tests against a REAL Postgres.

Why this file exists: the INSERT in api/v1/relationships.py is plain SQL, and
nothing without a database can say whether its column names, types and NOT NULL
constraints match the real table. A fake would only repeat our own assumptions --
and those were wrong twice already (UUID instead of text, and the two `sync_*`
columns below).

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
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from api.v1.relationships import INSERT_CHANGELOG, PENDING
from core.config import Settings
from core.errors import ConflictError
from db.models import Changelog
from db.sources import Sources
from db.sql import create_engine, create_sessionmaker, dispose_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("SQL_HOST"),
    reason="SQL_HOST is not set -- integration tests skipped.",
)

# Deliberately WITHOUT defaults on `user_id`, `session_id`, `sync_status` and
# `sync_attempts`: the model declares those as Python defaults, which SQLModel
# does not turn into DDL. Only `created_at` has a real server default. That is
# exactly the shape our INSERT has to survive.
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


async def _delete(engine: AsyncEngine, changelog_id: object) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM changelog WHERE changelog_id = :id"), {"id": changelog_id}
        )


async def test_the_insert_matches_the_real_table(sources: Sources, engine: AsyncEngine) -> None:
    """The whole point of this file: column names, types and NOT NULL, checked
    against a database instead of against our own fake."""
    changelog_id = uuid4()
    try:
        [written] = await sources.postgres(
            INSERT_CHANGELOG,
            changelog_id=changelog_id,
            user_id="a.schmidt",
            change_type="MATERIALS_RELATIONSHIP_CREATED",
            payload=json.dumps(RELATIONSHIP),
            session_id="unknown",
            sync_status=PENDING,
        )
        await sources.commit()

        assert written["changelog_id"] == changelog_id
        assert written["created_at"] is not None, "the column's server default did not fire"

        row = await _row(engine, changelog_id)
        assert row["user_id"] == "a.schmidt"
        assert row["change_type"] == "MATERIALS_RELATIONSHIP_CREATED"
        assert row["session_id"] == "unknown"
        assert row["sync_status"] == PENDING
        assert row["sync_attempts"] == 0
        assert row["synced_at"] is None

        # The driver may hand a json column back as text or as a dict.
        payload = row["payload"]
        assert (json.loads(payload) if isinstance(payload, str) else payload) == RELATIONSHIP
    finally:
        await _delete(engine, changelog_id)


async def test_leaving_out_sync_status_violates_not_null(sources: Sources) -> None:
    """Why `sync_status` is in the INSERT at all.

    `Field(default="pending")` is a PYTHON default on the model class: it fills
    the value when a row is created through that class, and it never reaches the
    DDL. An INSERT straight to the table therefore has to set it -- and our
    error translation turns the violation into a ConflictError (409).
    """
    with pytest.raises(ConflictError):
        await sources.postgres(
            """
            INSERT INTO changelog (changelog_id, user_id, change_type, payload,
                                   session_id, sync_attempts)
            VALUES (:changelog_id, 'system', 'X', '{}', 'unknown', 0)
            """,
            changelog_id=uuid4(),
        )


async def test_the_orm_write_path_reaches_the_same_table(
    sources: Sources, engine: AsyncEngine
) -> None:
    """Variant B (api/v1/relationships_orm.py) against the real table.

    Two things only a database can confirm: that the model's Python defaults do
    end up in the row, and that `refresh()` reads the server-generated
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
        assert row["sync_status"] == PENDING       # from the class, not from the route
        assert row["sync_attempts"] == 0
    finally:
        await _delete(engine, entry.changelog_id)
