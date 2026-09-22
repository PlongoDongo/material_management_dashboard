"""
Reading and changing Postgres through the table classes (db/models.py).

`Sources` is the one place that owns the session, so these tests check the
promises that come with it: one session per request, so everything a request
does is one transaction -- and the same error translation as for hand-written
SQL, so a constraint violation is a 409 and an outage a 503.

The session itself is a double here. Whether a `where(...)` really filters is
a question for a database: tests/test_integration_postgres.py.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any, Self

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import select

from core.config import Settings
from core.errors import ConflictError, UpstreamUnavailableError
from db.models import Changelog
from db.sources import Sources


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> Self:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def mappings(self) -> list[Any]:
        return []              # only used by the SQL path in the shared-session test


class _Session:
    """Records what it was asked to do; raises `error` if one was given."""

    def __init__(self, rows: list[Any] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.executed: list[Any] = []
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.flushes = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    async def execute(self, statement: Any, *args: object) -> _Result:  # noqa: ANN401
        self.executed.append(statement)
        self._maybe_fail()
        return _Result(self.rows)

    async def get(self, model: type, key: Any) -> Any:  # noqa: ANN401
        self.executed.append((model, key))
        self._maybe_fail()
        return self.rows[0] if self.rows else None

    def add_all(self, rows: Any) -> None:  # noqa: ANN401
        self.added.extend(rows)

    async def delete(self, row: Any) -> None:  # noqa: ANN401
        self.deleted.append(row)

    async def flush(self) -> None:
        self.flushes += 1
        self._maybe_fail()

    async def refresh(self, row: Any) -> None:  # noqa: ANN401
        pass


def _sources(stack: AsyncExitStack, session: _Session) -> Sources:
    return Sources(stack=stack, settings=Settings(_env_file=None), neo4j_driver=None,
                   sql_sessionmaker=lambda: session)         # type: ignore[arg-type]


async def _with(session: _Session, work: Any) -> Any:  # noqa: ANN401
    async with AsyncExitStack() as stack:
        return await work(_sources(stack, session))


def test_reading_hands_back_the_objects() -> None:
    entry = Changelog(change_type="X")
    session = _Session(rows=[entry])

    rows = asyncio.run(_with(session, lambda s: s.exec(select(Changelog))))

    assert rows == [entry]
    assert session.executed, "the statement never reached the session"


def test_get_looks_up_by_primary_key() -> None:
    entry = Changelog(change_type="X")
    session = _Session(rows=[entry])

    found = asyncio.run(_with(session, lambda s: s.get(Changelog, entry.changelog_id)))

    assert found is entry
    assert session.executed == [(Changelog, entry.changelog_id)]


def test_deleting_sends_the_statement_right_away() -> None:
    """`flush()` here rather than at commit time: a foreign key that refuses the
    delete has to surface at the route, not in the request scope."""
    entry = Changelog(change_type="X")
    session = _Session()

    asyncio.run(_with(session, lambda s: s.delete(entry)))

    assert session.deleted == [entry]
    assert session.flushes == 1


def test_everything_in_one_request_shares_one_session() -> None:
    """Which is what makes it one transaction: a read, a write and a change
    either land together or not at all."""
    session = _Session(rows=[Changelog(change_type="X")])
    opened = 0

    def sessionmaker() -> _Session:
        nonlocal opened
        opened += 1
        return session

    async def work() -> None:
        async with AsyncExitStack() as stack:
            sources = Sources(stack=stack, settings=Settings(_env_file=None), neo4j_driver=None,
                              sql_sessionmaker=sessionmaker)    # type: ignore[arg-type]
            # Deliberately interleaved: each of the three has to pick up the
            # session the previous one opened, not just the first one.
            await sources.postgres("SELECT 1")
            await sources.exec(select(Changelog))
            await sources.add(Changelog(change_type="Y"))
            await sources.exec(select(Changelog))

    asyncio.run(work())

    assert opened == 1


@pytest.mark.parametrize(("error", "expected"), [
    (IntegrityError("stmt", {}, Exception("duplicate key")), ConflictError),
    (OperationalError("stmt", {}, Exception("server closed")), UpstreamUnavailableError),
])
def test_a_failure_while_reading_is_translated_too(
    error: Exception, expected: type[Exception]
) -> None:
    """The ORM path goes through the same translation as `postgres(...)` --
    otherwise a database outage during a read would look like a bug (500)."""
    session = _Session(error=error)

    with pytest.raises(expected):
        asyncio.run(_with(session, lambda s: s.exec(select(Changelog))))
