"""
Access to the data sources.

A data product receives exactly one object -- `Sources` -- and runs its queries
through it:

    rows = await sources.neo4j(CYPHER)
    rows = await sources.postgres(SQL, since=params.since)

That is all there is to know. `Sources` takes care of three things every data
product would otherwise have to get right on its own:

  1. Connections open lazily. A product that only queries the graph never opens
     a Postgres connection.
  2. Two queries in the same request share one connection.
  3. Everything is closed reliably at the end -- even if a query raises. That is
     why no data product ever contains `session.close()`.

A `Sources` object lives for exactly one HTTP request (see api/deps.py).
"""
from __future__ import annotations

import base64
import logging
from contextlib import AsyncExitStack
from typing import Any

from neo4j import AsyncDriver
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from neo4j.graph import Entity, Path
from neo4j.spatial import Point
from neo4j.time import Date, DateTime, Duration, Time
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from data_api.core.config import Settings
from data_api.core.errors import ConfigurationError, UpstreamUnavailableError
from data_api.db.sql import SessionMaker

log = logging.getLogger(__name__)

# One result row is a plain dict: column name -> value.
Row = dict[str, Any]


def _to_python_value(value: Any) -> Any:
    """Translates Neo4j-specific types into ones Pydantic and JSON understand.

    The driver returns its own classes for several property types. Without this
    conversion one of two things happens -- both unpleasant:

        neo4j.time.Date      -> Pydantic rejects it (even for a date field!):
                                "Input should be a valid date"
        neo4j.time.Duration  -> subclasses tuple, silently becomes [3,2,0,0]
        neo4j.spatial.Point  -> subclasses tuple, silently becomes [1.0,2.0]
        neo4j.graph.Path     -> passes through as a driver object; FastAPI falls
                                back to vars() and leaks private attribute names

    The silent cases are the dangerous ones: no error, but the meaning is gone.
    That is why everything is translated explicitly.

    Recursive, because `collect()` and map projections produce nested lists and
    dicts.
    """
    # Order matters: Duration and Point subclass tuple, so they must be checked
    # BEFORE the generic container handling below.
    if isinstance(value, (Date, DateTime, Time)):
        return value.to_native()             # -> datetime.date / .datetime / .time
    if isinstance(value, Duration):
        return value.iso_format()            # -> "P3M2DT1M30S"
    if isinstance(value, Point):
        # `z` is ALWAYS present (None for 2D points). Otherwise a 2D point would
        # have the shape {"srid","x","y"} and a 3D one {"srid","x","y","z"} --
        # a response mixing both would contain inconsistent objects, and a row
        # model with `z: float | None` could not be satisfied.
        coordinates = dict(zip(("x", "y", "z"), tuple(value)))
        return {"srid": value.srid, "x": coordinates.get("x"),
                "y": coordinates.get("y"), "z": coordinates.get("z")}
    if isinstance(value, Path):
        # A path is NOT an Entity and would otherwise pass through untranslated
        # -- the obvious return value for bills of materials and supply chains.
        return {
            "nodes": [_to_python_value(node) for node in value.nodes],
            "relationships": [_to_python_value(rel) for rel in value.relationships],
        }
    if isinstance(value, Entity):
        # Node or Relationship. Properties only -- labels, type and element id
        # are lost. Prefer returning the fields you need explicitly in Cypher
        # (RETURN m.nr AS number) over returning the whole node (RETURN m).
        return {name: _to_python_value(v) for name, v in dict(value).items()}
    if type(value).__module__.split(".")[0] == "neo4j":
        # Safety net -- MUST come before the container handling below: several
        # driver types subclass tuple and would silently turn into lists (the
        # exact Duration/Point failure one level up). A driver type this
        # function does not know would otherwise be passed through unchanged,
        # which is precisely the bug it exists to prevent.
        raise TypeError(
            f"Untranslatable Neo4j type: {type(value).__module__}."
            f"{type(value).__name__}. Add it to db/sources.py::_to_python_value "
            f"or convert it to a plain value in the Cypher query."
        )
    if isinstance(value, dict):
        return {name: _to_python_value(v) for name, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_python_value(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("ascii")
    return value


class Sources:
    """Lives for exactly one request."""

    def __init__(
        self,
        stack: AsyncExitStack,
        settings: Settings,
        neo4j_driver: AsyncDriver | None,
        sql_sessionmaker: SessionMaker | None,
    ) -> None:
        self._stack = stack
        self._settings = settings
        self._driver = neo4j_driver
        self._sessionmaker = sql_sessionmaker
        self._sessions: dict[str, Any] = {}
        # Which sources this request actually used -> ends up in meta.source.
        self.used: set[str] = set()

    async def neo4j(self, cypher: str, **parameters: Any) -> list[Row]:
        """Runs a Cypher query and returns its rows.

            rows = await sources.neo4j("MATCH (m:Material) RETURN m.nr AS number")

        Parameters are passed by name, NOT spliced into the query text --
        `$since` in the Cypher, `since=...` here. That is faster (the database
        can reuse the query plan) and safe.

        Neo4j-specific types (Date, Duration, Point, ...) are converted into
        plain Python values -- see `_to_python_value`.
        """
        if self._driver is None:
            raise ConfigurationError(
                "Neo4j is not configured (NEO4J_URI is missing) but is required here."
            )
        if "neo4j" not in self._sessions:
            self._sessions["neo4j"] = await self._stack.enter_async_context(
                self._driver.session(database=self._settings.neo4j_db)
            )
        self.used.add("neo4j")

        # `parameters=` explicitly rather than `**parameters`: the driver's
        # signature is `run(query, parameters=None, **kwargs)`. A Cypher
        # parameter that happens to be called `query` or `parameters` would
        # otherwise land in the driver's own slot instead of in the query --
        # once as a TypeError, once as a silent mix-up.
        try:
            result = await self._sessions["neo4j"].run(cypher, parameters=parameters)
            # Deliberately not `result.data()`: that would silently flatten
            # nodes to properties without giving us a chance to translate the
            # values inside them.
            return [
                {name: _to_python_value(value) for name, value in record.items()}
                async for record in result
            ]
        except (ServiceUnavailable, SessionExpired, OSError) as error:
            # 503 rather than 500: for the dashboard that is the difference
            # between "try again later" and "please report this as a bug".
            raise UpstreamUnavailableError(f"Neo4j unreachable: {error}") from error

    async def postgres(self, sql: str, **parameters: Any) -> list[Row]:
        """Runs a SQL query and returns its rows.

            rows = await sources.postgres("SELECT * FROM x WHERE d >= :since", since=...)

        Same rule as above: `:name` in the SQL, `name=...` here. Never splice
        values into the text -- that would be a SQL injection hole.
        """
        if self._sessionmaker is None:
            raise ConfigurationError(
                "Postgres is not configured (POSTGRES_DSN is missing) but is required here."
            )
        if "sql" not in self._sessions:
            self._sessions["sql"] = await self._stack.enter_async_context(
                self._sessionmaker()
            )
        self.used.add("postgres")
        try:
            result = await self._sessions["sql"].execute(text(sql), parameters)
            return [dict(row) for row in result.mappings()]
        except (OperationalError, DBAPIError, OSError) as error:
            raise UpstreamUnavailableError(f"Postgres unreachable: {error}") from error

    async def commit(self) -> None:
        """Commits the SQL transaction. Called by the request scope.

        Without this call SQLAlchemy rolls back when the session closes. For the
        read-only side that is harmless -- but anyone following the TODO in
        api/v1/mappings.py and adding an INSERT would get an endpoint that
        answers 201, invalidates the cache, logs success and writes nothing.
        Silent again, plausible-looking again.

        Called ONLY on the success path (see api/deps.py): if the endpoint
        raises, the AsyncExitStack rolls back instead.
        """
        session = self._sessions.get("sql")
        if session is not None:
            await session.commit()

    @property
    def label(self) -> str:
        """For meta.source in the response, e.g. "neo4j+postgres"."""
        return "+".join(sorted(self.used)) or "none"
