"""
Material-Repository: Port + Adapter.

    MaterialsRepository       <- der PORT (Protocol): was der Rest der App darf
    Neo4jMaterialsRepository  <- der ADAPTER auf die echte Datenquelle

Datenprodukte und Router kennen ausschliesslich den Port, nie den Adapter.
Das hat zwei Konsequenzen, die sich im Alltag auszahlen:

  * Aendert sich das Graphmodell, ist genau diese Datei zu pflegen -- kein
    Datenprodukt und kein Dashboard merkt etwas davon.
  * In Tests tritt ein einfaches Objekt an die Stelle des Adapters
    (siehe tests/fakes.py). Die Tests brauchen deshalb keine laufende Neo4j.

Ein Repository liefert ROHE Zeilen (list[dict]), keine fertigen Datenprodukte.
Die fachliche Formung passiert in der Produkt-Schicht. Sonst braucht jedes neue
Dashboard eine neue Repository-Methode.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from neo4j import AsyncSession

log = logging.getLogger(__name__)

# --- Cypher ----------------------------------------------------------------
# Die Queries stehen bewusst hier und nicht im Datenprodukt: mehrere Produkte
# teilen sich dieselbe Rohabfrage, und wenn sich das Graphmodell aendert, ist
# genau diese Datei die eine Stelle dafuer.
_MATERIALS_CYPHER = """
MATCH (m:Material)
OPTIONAL MATCH (m)-[:HAS_WARENGRUPPE]->(w:Warengruppe)
OPTIONAL MATCH (m)-[:LOCATED_IN]->(werk:Werk)
RETURN m.nr        AS material_nr,
       m.name      AS bezeichnung,
       w.name      AS warengruppe,
       werk.id     AS werk_id,
       werk.name   AS werk_name,
       m.status    AS status,
       m.einheit   AS einheit,
       m.bestand   AS bestand,
       m.preis     AS preis,
       m.geaendert AS geaendert
ORDER BY m.nr
"""

_SUPPLIER_LINK_CYPHER = """
MATCH (s:Lieferant)-[:SUPPLIES]->(m:Material)
RETURN s.id       AS lieferant_id,
       s.name     AS lieferant_name,
       s.land     AS land,
       count(m)   AS anzahl_materialien
ORDER BY s.id
"""


@runtime_checkable
class MaterialsRepository(Protocol):
    """Der Port. Alles, was die API ueber Materialdaten wissen darf."""

    source: str

    async def fetch_materials(self) -> list[dict[str, Any]]: ...

    async def fetch_suppliers(self) -> list[dict[str, Any]]: ...


class Neo4jMaterialsRepository:
    """Der Adapter. Bekommt die Session herein (Dependency Injection).

    Die Session wird NICHT hier geoeffnet -- das macht der Request-Scope
    (db/repositories.py). Dadurch teilen sich mehrere Repositories eine Session
    und der Lebenszyklus haengt an genau einer Stelle.
    """

    source = "neo4j"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        result = await self._session.run(cypher, **params)
        return await result.data()   # list[dict] -- schon JSON-nah

    async def fetch_materials(self) -> list[dict[str, Any]]:
        return await self._run(_MATERIALS_CYPHER)

    async def fetch_suppliers(self) -> list[dict[str, Any]]:
        return await self._run(_SUPPLIER_LINK_CYPHER)
