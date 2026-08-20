"""
Request-Scope: welches Repository bekommt ein Datenprodukt, und wer macht die
Session wieder zu?

`Repositories` ist der EINE Zugriffspunkt, den ein Datenprodukt sieht. Er
  * oeffnet Sessions LAZY (ein Produkt, das nur Neo4j braucht, oeffnet keine
    Postgres-Verbindung),
  * cached sie pro Request (zwei Repositories -> eine Session),
  * schliesst alles zuverlaessig ueber einen AsyncExitStack.

Warum ein Container statt "jedes Produkt deklariert seine eigenen Depends"?
Weil die Datenprodukt-Routen generisch erzeugt werden (products/router.py) --
sie koennen die individuelle Signatur eines Produkt-Loaders nicht kennen. Der
Container ist die eine Abhaengigkeit, die alle Produkte gemeinsam haben.
Ein neues Repository = eine neue Methode hier.

Fehlt eine Datenquelle, wird sofort und laut abgebrochen. Es gibt bewusst
KEINEN Ersatzdatensatz im Produktionspfad: eine API, die stillschweigend
erfundene Zahlen liefert, ist gefaehrlicher als eine, die ehrlich einen Fehler
meldet. Mock-Daten fuer noch fehlende Quellen gehoeren in die Datenbank
(siehe seed/), Testdaten in die Tests (siehe tests/fakes.py).
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from neo4j import AsyncDriver, AsyncSession

from data_api.core.config import Settings
from data_api.core.errors import ConfigurationError
from data_api.db.sql import SessionMaker
from data_api.repositories.deliveries import DeliveriesRepository, SqlDeliveriesRepository
from data_api.repositories.materials import MaterialsRepository, Neo4jMaterialsRepository

log = logging.getLogger(__name__)


class Repositories:
    """Lebt genau einen Request lang."""

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
        self._cache: dict[str, Any] = {}
        # Welche Quellen dieser Request benutzt hat -> landet in den
        # Response-Metadaten (meta.source).
        self.sources_used: set[str] = set()

    # --- Sessions ----------------------------------------------------------
    async def _neo4j_session(self) -> AsyncSession:
        if self._driver is None:
            raise ConfigurationError(
                "Neo4j ist nicht konfiguriert (NEO4J_URI fehlt). Dieses "
                "Datenprodukt braucht den Graphen."
            )
        if "neo4j" not in self._cache:
            self._cache["neo4j"] = await self._stack.enter_async_context(
                self._driver.session(database=self._settings.neo4j_db)
            )
        return self._cache["neo4j"]

    async def _sql_session(self) -> Any:
        if self._sessionmaker is None:
            raise ConfigurationError(
                "Postgres ist nicht konfiguriert (POSTGRES_DSN fehlt). Dieses "
                "Datenprodukt braucht die relationale Datenbank."
            )
        if "sql" not in self._cache:
            self._cache["sql"] = await self._stack.enter_async_context(self._sessionmaker())
        return self._cache["sql"]

    # --- Repositories ------------------------------------------------------
    async def materials(self) -> MaterialsRepository:
        session = await self._neo4j_session()
        self.sources_used.add(Neo4jMaterialsRepository.source)
        return Neo4jMaterialsRepository(session)

    async def deliveries(self) -> DeliveriesRepository:
        session = await self._sql_session()
        self.sources_used.add(SqlDeliveriesRepository.source)
        return SqlDeliveriesRepository(session)

    @property
    def source_label(self) -> str:
        return "+".join(sorted(self.sources_used)) or "none"
