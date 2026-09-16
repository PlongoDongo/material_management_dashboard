"""
Errors: one shape for every failure, and a status that says what to DO.

    503  retry later     409  change the input     500  report a bug

Both halves used to be possible to get wrong without anything raising: a
duplicate key reported as "Postgres unreachable" (retry forever), a Neo4j
DatabaseUnavailable reported as a bug, and an unexpected exception answered in
plain text with no request id to find it in the logs by.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import (
    ConstraintError,
    CypherSyntaxError,
    DatabaseUnavailable,
    ServiceUnavailable,
    SessionExpired,
)
from sqlalchemy.exc import (
    DataError,
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)

from app import create_app
from core.config import Settings
from core.errors import AppError, ConflictError, Problem, UpstreamUnavailableError
from db.sources import Sources

# --- Test doubles: a driver and a session that fail on demand ---------------

class _Result:
    def mappings(self) -> list[dict[str, Any]]:
        return []


class _Session:
    """Raises `error` from the one method named in `fails_on`."""

    def __init__(self, error: Exception, fails_on: str) -> None:
        self.error, self.fails_on = error, fails_on

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def _maybe_fail(self, method: str) -> None:
        if method == self.fails_on:
            raise self.error

    async def run(self, *args: object, **kwargs: object) -> None:
        self._maybe_fail("run")

    async def execute(self, *args: object, **kwargs: object) -> _Result:
        self._maybe_fail("execute")
        return _Result()

    async def commit(self) -> None:
        self._maybe_fail("commit")


class _Driver:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def session(self, **kwargs: object) -> _Session:
        return _Session(self.error, fails_on="run")


def _sql(error_class: type[DBAPIError]) -> DBAPIError:
    """A SQLAlchemy error the way SQLAlchemy builds it: statement, parameters,
    and the driver's own exception as `.orig`."""
    return error_class("INSERT INTO mapping VALUES (:nr)", {"nr": "MAT-1"},
                       Exception('duplicate key value violates unique constraint "mapping_pkey"'))


async def _run(source: str, error: Exception, fails_on: str = "execute") -> None:
    async with AsyncExitStack() as stack:
        sources = Sources(
            stack=stack,
            settings=Settings(_env_file=None),
            neo4j_driver=_Driver(error),                           # type: ignore[arg-type]
            sql_sessionmaker=lambda: _Session(error, fails_on),    # type: ignore[arg-type]
        )
        if source == "neo4j":
            await sources.neo4j("RETURN 1")
        else:
            await sources.postgres("SELECT 1")
            await sources.commit()


# --- Driver failures -> domain errors ---------------------------------------

@pytest.mark.parametrize(("error", "expected"), [
    (ServiceUnavailable("no route to host"), UpstreamUnavailableError),
    (SessionExpired("connection lost"), UpstreamUnavailableError),
    (DatabaseUnavailable("database is starting"), UpstreamUnavailableError),
    (ConnectionRefusedError("refused"), UpstreamUnavailableError),
    (ConstraintError("node already exists"), ConflictError),
    # A bug in our Cypher is not an outage: it stays what it is -> 500.
    (CypherSyntaxError("invalid input"), CypherSyntaxError),
], ids=lambda value: type(value).__name__ if isinstance(value, Exception) else "")
def test_neo4j_failures_are_translated(error: Exception, expected: type[Exception]) -> None:

    with pytest.raises(expected):
        asyncio.run(_run("neo4j", error))


@pytest.mark.parametrize(("error", "expected"), [
    (_sql(OperationalError), UpstreamUnavailableError),
    (_sql(InterfaceError), UpstreamUnavailableError),
    # asyncpg errors SQLAlchemy has no class for (too many connections, database
    # starting up) arrive as the plain base class.
    (_sql(DBAPIError), UpstreamUnavailableError),
    (ConnectionRefusedError("refused"), UpstreamUnavailableError),
    (_sql(IntegrityError), ConflictError),
    (_sql(ProgrammingError), ProgrammingError),
    (_sql(DataError), DataError),
], ids=lambda value: type(value).__name__ if isinstance(value, Exception) else "")
def test_postgres_failures_are_translated(error: Exception, expected: type[Exception]) -> None:

    with pytest.raises(expected):
        asyncio.run(_run("postgres", error))


def test_a_conflict_at_commit_time_is_a_conflict_too() -> None:
    """Deferred constraints and flushes fail in COMMIT, not in the statement."""

    with pytest.raises(ConflictError):
        asyncio.run(_run("postgres", _sql(IntegrityError), fails_on="commit"))


def test_a_conflict_names_the_constraint_but_not_the_statement() -> None:
    """str() of a SQLAlchemy error appends the SQL and its bound parameters --
    fine in a log, not in a response body."""

    with pytest.raises(ConflictError) as caught:
        asyncio.run(_run("postgres", _sql(IntegrityError)))

    assert "mapping_pkey" in caught.value.detail
    assert "[SQL:" not in caught.value.detail
    assert "MAT-1" not in caught.value.detail


# --- One response shape, whatever went wrong --------------------------------

def test_every_error_has_the_same_shape(settings: Settings) -> None:
    """What the handlers in core/errors.py are for.

    Without them FastAPI answers these four in three different formats: a
    plain-text 500 for both the domain error and the bug, and a 422 whose
    `detail` is a list instead of a string.
    """
    app = create_app(settings)

    @app.get("/boom/upstream")
    async def _upstream() -> None:
        raise UpstreamUnavailableError("Neo4j unavailable: no route to host")

    @app.get("/boom/bug")
    async def _bug() -> None:
        raise KeyError("a bug")

    with TestClient(app, raise_server_exceptions=False) as client:
        responses = {
            "upstream_unavailable": client.get("/boom/upstream"),
            "internal_error": client.get("/boom/bug"),
            "validation_error": client.get("/api/v1/data-products/material-overview/v3",
                                           params={"limit": "many"}),
            "http_error": client.get("/api/v1/does-not-exist"),
        }

    for code, response in responses.items():
        assert response.headers["content-type"].startswith("application/problem+json"), code
        body = response.json()
        assert body["code"] == code
        assert isinstance(body["detail"], str), code
        assert body["request_id"] and body["request_id"] == response.headers["X-Request-ID"]

    assert responses["upstream_unavailable"].status_code == 503
    assert responses["internal_error"].status_code == 500


# --- The documented shape is the shape that is sent --------------------------

def test_the_docs_describe_every_error_a_route_can_answer(settings: Settings) -> None:
    """Without this, /docs shows FastAPI's default 422 (`detail` as a list) and
    no 401/403/500/503 -- the dashboard would be built against a shape the API
    never sends."""
    schema = create_app(settings).openapi()
    product = schema["paths"]["/api/v1/data-products/material-overview/v3"]["get"]

    assert {"401", "403", "422", "500", "503"} <= set(product["responses"])
    assert "409" in schema["paths"]["/api/v1/mappings"]["post"]["responses"]
    assert "404" in schema["paths"]["/api/v1/catalog/{name}"]["get"]["responses"]

    # FastAPI documents a declared model under the route's default media type,
    # so `application/json` is listed next to it. Known and accepted -- see the
    # note in api/README.md.
    content = product["responses"]["422"]["content"]
    assert content["application/json"]["schema"]["$ref"].endswith("Problem")
    assert "HTTPValidationError" not in json.dumps(schema), "FastAPI's default 422 is still there"


def test_the_documented_model_matches_a_real_error_body(settings: Settings) -> None:
    """`_problem` builds the body FROM `Problem`, so /docs cannot describe
    something else. This pins the part the model alone does not say: which
    fields actually appear."""
    app = create_app(settings)

    @app.get("/boom")
    async def _boom() -> None:
        raise UpstreamUnavailableError("Neo4j unavailable: no route to host")

    with TestClient(app, raise_server_exceptions=False) as client:
        failed = client.get("/boom").json()
        invalid = client.get("/api/v1/data-products/material-overview/v3",
                             params={"limit": "many"}).json()

    # `errors` is the one optional member -- only a 422 carries it.
    assert set(failed) == set(Problem.model_fields) - {"errors"}
    assert set(invalid) == set(Problem.model_fields)
    assert invalid["errors"][0]["loc"] == ["query", "limit"]


def test_every_error_class_reaches_the_documentation(settings: Settings) -> None:
    """The one place a new error class can be forgotten.

    Declaring an error is a class; documenting it is passing that class to
    `documented_errors(...)` on the routes that answer it. This fails if a
    status code can reach a client that /docs never mentions.
    """
    schema = create_app(settings).openapi()
    documented = {
        code
        for path in schema["paths"].values()
        for operation in path.values()
        for code in operation.get("responses", {})
    }

    def subclasses(cls: type) -> set[type]:
        found = set(cls.__subclasses__())
        return found | {c for sub in found for c in subclasses(sub)}

    for error in {AppError, *subclasses(AppError)}:
        assert str(error.status_code) in documented, (
            f"{error.__name__} answers {error.status_code}, which no route documents -- "
            f"pass it to responses=documented_errors(...)"
        )
