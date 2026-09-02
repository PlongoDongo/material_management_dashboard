"""
material-overview v2 -- dieselbe Fachlichkeit, brechend geaendertes Schema.

Aenderungen gegenueber v1, und warum jede davon MAJOR erzwingt:

    werk        -> werk_id + werk_name    Feld ersetzt   -> brechend
    einheit     -> entfaellt              Feld entfernt  -> brechend
    bestandswert (neu, berechnet)         Feld ergaenzt  -> allein waere MINOR

v1 und v2 liegen als eigene Dateien nebeneinander -- jede mit ihrer eigenen
Abfrage. Das ist Absicht: v2 darf den Cypher aendern, ohne v1 anzufassen.
"""
from __future__ import annotations

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
       werk.id     AS werk_id,
       werk.name   AS werk_name,
       m.status    AS status,
       m.bestand   AS bestand,
       m.preis     AS preis,
       m.geaendert AS geaendert
ORDER BY m.nr
"""


# 2. Der Vertrag.
class MaterialRowV2(BaseModel):
    material_nr: str
    bezeichnung: str | None = None
    warengruppe: str | None = None
    werk_id: str | None = Field(None, description="Neu in v2 -- stabiler Schluessel.")
    werk_name: str | None = None
    status: str | None = None
    bestand: int | None = None
    preis: float | None = None
    bestandswert: float | None = Field(
        None, description="Berechnet: bestand * preis. Wird nicht gespeichert."
    )
    geaendert: str | None = None


# 3. Die erlaubten Filter.
class MaterialParamsV2(ProductParams):
    status: list[str] | None = None
    werk_id: list[str] | None = Field(None, description="Filtert ueber die ID, nicht den Namen.")
    warengruppe: list[str] | None = None
    ohne_klassifizierung: bool = False
    suche: str | None = None
    min_bestandswert: float | None = Field(
        None, ge=0, description="Nur Positionen ab diesem Bestandswert."
    )


# 4. Die Fachlichkeit -- rein, ohne Datenbank, ohne HTTP.
def transform(rows: list[dict[str, Any]], params: MaterialParamsV2) -> list[dict[str, Any]]:
    ergebnis: list[dict[str, Any]] = []
    suche = (params.suche or "").strip().lower()

    for row in rows:
        warengruppe = row.get("warengruppe") or None
        bestand = row.get("bestand")
        preis = row.get("preis")
        # None heisst "unbekannt", nicht "null" -- deshalb wird hier nicht
        # auf 0 gerundet, sondern der Wert bleibt leer.
        bestandswert = (
            round(float(bestand) * float(preis), 2)
            if bestand is not None and preis is not None
            else None
        )

        if params.status and row.get("status") not in params.status:
            continue
        if params.werk_id and row.get("werk_id") not in params.werk_id:
            continue
        if params.warengruppe and warengruppe not in params.warengruppe:
            continue
        if params.ohne_klassifizierung and warengruppe is not None:
            continue
        if params.min_bestandswert is not None and (bestandswert or 0) < params.min_bestandswert:
            continue
        if suche and suche not in f"{row.get('material_nr', '')} {row.get('bezeichnung', '')}".lower():
            continue

        ergebnis.append({
            "material_nr": row["material_nr"],
            "bezeichnung": row.get("bezeichnung"),
            "warengruppe": warengruppe,
            "werk_id": row.get("werk_id"),
            "werk_name": row.get("werk_name"),
            "status": row.get("status"),
            "bestand": int(bestand) if bestand is not None else None,
            "preis": float(preis) if preis is not None else None,
            "bestandswert": bestandswert,
            "geaendert": row.get("geaendert"),
        })
    return ergebnis


# 5. Die Verdrahtung.
async def load(sources: Sources, params: MaterialParamsV2) -> list[dict[str, Any]]:
    """Materialstammdaten mit getrennter Werks-ID und berechnetem Bestandswert."""
    return transform(await sources.neo4j(CYPHER), params)


# 6. Veroeffentlichen.
registry.add(DataProduct(
    name="material-overview",
    version="2.0",
    summary="Materialstammdaten inkl. Bestandswert",
    item_model=MaterialRowV2,
    params_model=MaterialParamsV2,
    loader=load,
    owner="team-material-management",
    tags=("material", "stammdaten"),
    cache_ttl=60,
))
