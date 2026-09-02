"""
Der Zugang zu den Datenquellen.

Ein Datenprodukt bekommt genau ein Objekt herein -- `Sources` -- und stellt
damit seine Abfragen:

    rows = await sources.neo4j(CYPHER)
    rows = await sources.postgres(SQL, seit=params.seit)

Mehr gibt es nicht zu wissen. `Sources` kuemmert sich um drei Dinge, die man
sonst in jedem Datenprodukt neu richtig machen muesste:

  1. Die Verbindung wird erst geoeffnet, wenn sie gebraucht wird. Ein Produkt,
     das nur den Graphen abfragt, oeffnet keine Postgres-Verbindung.
  2. Zwei Abfragen im selben Request teilen sich eine Verbindung.
  3. Alles wird am Ende zuverlaessig geschlossen -- auch wenn die Abfrage
     einen Fehler wirft. Darum steht in keinem Datenprodukt je `session.close()`.

Ein `Sources`-Objekt lebt genau einen HTTP-Request lang (siehe api/deps.py).
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from neo4j import AsyncDriver
from sqlalchemy import text

from data_api.core.config import Settings
from data_api.core.errors import ConfigurationError
from data_api.db.sql import SessionMaker

log = logging.getLogger(__name__)

# Eine Ergebniszeile ist ein einfaches dict: Spaltenname -> Wert.
Row = dict[str, Any]


class Sources:
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
        # Welche Quellen dieser Request benutzt hat -> landet in meta.source.
        self.used: set[str] = set()

    async def neo4j(self, cypher: str, **parameter: Any) -> list[Row]:
        """Fuehrt eine Cypher-Abfrage aus und gibt die Zeilen zurueck.

            rows = await sources.neo4j("MATCH (m:Material) RETURN m.nr AS nr")

        Parameter werden als benannte Werte uebergeben, NICHT in den Text
        eingesetzt -- `$seit` im Cypher, `seit=...` hier. Das ist schneller
        (Neo4j kann den Abfrageplan wiederverwenden) und sicher.
        """
        if self._driver is None:
            raise ConfigurationError(
                "Neo4j ist nicht konfiguriert (NEO4J_URI fehlt), wird aber gebraucht."
            )
        if "neo4j" not in self._sessions:
            self._sessions["neo4j"] = await self._stack.enter_async_context(
                self._driver.session(database=self._settings.neo4j_db)
            )
        self.used.add("neo4j")
        result = await self._sessions["neo4j"].run(cypher, **parameter)
        return await result.data()

    async def postgres(self, sql: str, **parameter: Any) -> list[Row]:
        """Fuehrt eine SQL-Abfrage aus und gibt die Zeilen zurueck.

            rows = await sources.postgres("SELECT * FROM x WHERE d >= :seit", seit=...)

        Wie oben: `:name` im SQL, `name=...` hier. Werte niemals in den Text
        einsetzen -- das waere eine SQL-Injection-Luecke.
        """
        if self._sessionmaker is None:
            raise ConfigurationError(
                "Postgres ist nicht konfiguriert (POSTGRES_DSN fehlt), wird aber gebraucht."
            )
        if "sql" not in self._sessions:
            self._sessions["sql"] = await self._stack.enter_async_context(
                self._sessionmaker()
            )
        self.used.add("postgres")
        result = await self._sessions["sql"].execute(text(sql), parameter)
        return [dict(row) for row in result.mappings()]

    @property
    def label(self) -> str:
        """Fuer meta.source in der Antwort, z. B. 'neo4j+postgres'."""
        return "+".join(sorted(self.used)) or "none"
