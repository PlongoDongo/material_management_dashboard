"""
Lieferungs-Repository (Postgres). Gleiches Muster wie materials.py.

Zeigt den zweiten Fall: nicht alle Daten liegen im Graphen. Ein Datenprodukt
darf beide Quellen kombinieren -- genau das ist einer der Hauptgruende fuer
einen API-Layer: die Verknuepfung passiert EINMAL hier, statt in jedem
Dashboard neu.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Rohes SQL statt ORM-Modellen: Datenprodukte lesen aggregiert, sie mappen keine
# Entitaeten. ORM-Modelle lohnen sich fuer die SCHREIBENDE Seite (siehe
# migrations/ + api/v1/mappings.py), nicht fuer Read-Only-Analytics.
_DELIVERIES_SQL = text("""
SELECT lieferant_id,
       material_nr,
       geliefert_am,
       zugesagt_am,
       menge,
       reklamationen
FROM   lieferungen
WHERE  geliefert_am >= :seit
ORDER  BY lieferant_id, geliefert_am
""")


@runtime_checkable
class DeliveriesRepository(Protocol):
    source: str

    async def fetch_deliveries(self, seit: dt.date) -> list[dict[str, Any]]: ...


class SqlDeliveriesRepository:
    source = "postgres"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_deliveries(self, seit: dt.date) -> list[dict[str, Any]]:
        result = await self._session.execute(_DELIVERIES_SQL, {"seit": seit})
        return [dict(row) for row in result.mappings()]
