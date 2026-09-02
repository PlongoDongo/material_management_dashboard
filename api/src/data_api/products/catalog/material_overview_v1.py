"""
material-overview v1 -- die Materialtabelle des Material-Management-Dashboards.

ABGEKUENDIGT: v2 ist da. Diese Datei bleibt unveraendert, bis das Sunset-Datum
erreicht ist und die Zugriffslogs zeigen, dass niemand mehr /v1 abfragt --
danach wird sie geloescht, und Route, Doku und Katalogeintrag verschwinden mit.

Eine noch benutzte Version wird NICHT mehr angefasst. "Wir patchen v1 noch
schnell, es nutzt ja nur ein Dashboard" ist der uebliche Weg, auf dem
Versionierung scheitert.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from data_api.db.sources import Sources
from data_api.products.base import DataProduct, ProductParams
from data_api.products.registry import registry

# 1. Die Abfrage.
CYPHER = """
MATCH (m:Material)
OPTIONAL MATCH (m)-[:HAS_WARENGRUPPE]->(w:Warengruppe)
OPTIONAL MATCH (m)-[:LOCATED_IN]->(werk:Werk)
RETURN m.nr        AS material_nr,
       m.name      AS bezeichnung,
       w.name      AS warengruppe,
       werk.name   AS werk,
       m.status    AS status,
       m.einheit   AS einheit,
       m.bestand   AS bestand,
       m.geaendert AS geaendert
ORDER BY m.nr
"""


# 2. Der Vertrag: genau diese Felder bekommt das Dashboard.
class MaterialRowV1(BaseModel):
    material_nr: str = Field(description="Eindeutige Materialnummer.")
    bezeichnung: str | None = None
    warengruppe: str | None = Field(None, description="Leer/None = ohne Klassifizierung.")
    werk: str | None = None
    status: str | None = Field(None, description="Aktiv | Nicht geliefert | Obsolet | Gesperrt")
    einheit: str | None = None
    bestand: int | None = None
    geaendert: str | None = None


# 3. Die erlaubten Filter.
class MaterialParamsV1(ProductParams):
    status: list[str] | None = Field(None, description="Mehrfachauswahl (ODER-verknuepft).")
    werk: list[str] | None = None
    warengruppe: list[str] | None = None
    ohne_klassifizierung: bool = Field(False, description="Nur Material ohne Warengruppe.")
    suche: str | None = Field(None, description="Freitext in Material-Nr. oder Bezeichnung.")


# 4. Die Fachlichkeit -- rein, ohne Datenbank, ohne HTTP.
def transform(rows: list[dict[str, Any]], params: MaterialParamsV1) -> list[dict[str, Any]]:
    ergebnis: list[dict[str, Any]] = []
    suche = (params.suche or "").strip().lower()

    for row in rows:
        warengruppe = row.get("warengruppe") or None

        if params.status and row.get("status") not in params.status:
            continue
        if params.werk and row.get("werk") not in params.werk:
            continue
        if params.warengruppe and warengruppe not in params.warengruppe:
            continue
        if params.ohne_klassifizierung and warengruppe is not None:
            continue
        if suche and suche not in f"{row.get('material_nr', '')} {row.get('bezeichnung', '')}".lower():
            continue

        ergebnis.append({
            "material_nr": row["material_nr"],
            "bezeichnung": row.get("bezeichnung"),
            "warengruppe": warengruppe,
            "werk": row.get("werk"),
            "status": row.get("status"),
            "einheit": row.get("einheit"),
            "bestand": int(row["bestand"]) if row.get("bestand") is not None else None,
            "geaendert": row.get("geaendert"),
        })
    return ergebnis


# 5. Die Verdrahtung -- bewusst langweilig.
async def load(sources: Sources, params: MaterialParamsV1) -> list[dict[str, Any]]:
    """Materialstammdaten inkl. Warengruppe und Werk."""
    return transform(await sources.neo4j(CYPHER), params)


# 6. Veroeffentlichen.
registry.add(DataProduct(
    name="material-overview",
    version="1.2",
    summary="Materialstammdaten fuer die Uebersichtstabelle",
    item_model=MaterialRowV1,
    params_model=MaterialParamsV1,
    loader=load,
    owner="team-material-management",
    tags=("material", "stammdaten"),
    cache_ttl=60,
    deprecated=True,
    sunset=dt.date(2026, 12, 31),
))
