"""
material-overview v2 -- dieselbe Fachlichkeit, brechend geaendertes Schema.

Die Aenderungen gegenueber v1, und warum jede davon MAJOR erzwingt:

    werk        -> werk_id + werk_name    Feld ersetzt   -> brechend
    einheit     -> entfaellt              Feld entfernt  -> brechend
    bestandswert (neu, berechnet)         Feld ergaenzt  -> allein waere MINOR

Beide Versionen liegen als eigene Dateien nebeneinander und teilen sich das
Repository. Das ist wichtiger als es aussieht: v1 zu "patchen, solange es noch
jemand benutzt" ist der Weg, auf dem Versionierung scheitert. Solange v1
existiert, bleibt v1 unveraendert.

Aufraeumen: sobald `Sunset` erreicht ist und die Logs zeigen, dass niemand mehr
v1 abfragt, wird material_overview_v1.py geloescht. Route und Doku verschwinden
automatisch mit.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from data_api.db.repositories import Repositories
from data_api.products.base import ProductParams
from data_api.products.registry import data_product


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


class MaterialParamsV2(ProductParams):
    status: list[str] | None = None
    werk_id: list[str] | None = Field(None, description="Filtert ueber die ID, nicht den Namen.")
    warengruppe: list[str] | None = None
    ohne_klassifizierung: bool = False
    suche: str | None = None
    min_bestandswert: float | None = Field(
        None, ge=0, description="Nur Positionen ab diesem Bestandswert."
    )


def transform(rows: list[dict[str, Any]], params: MaterialParamsV2) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    needle = (params.suche or "").strip().lower()

    for row in rows:
        warengruppe = row.get("warengruppe") or None
        bestand = row.get("bestand")
        preis = row.get("preis")
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
        if needle:
            haystack = f"{row.get('material_nr', '')} {row.get('bezeichnung', '')}".lower()
            if needle not in haystack:
                continue

        result.append({
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
    return result


@data_product(
    name="material-overview",
    version="2.0",
    summary="Materialstammdaten inkl. Bestandswert",
    item_model=MaterialRowV2,
    params_model=MaterialParamsV2,
    owner="team-material-management",
    tags=("material", "stammdaten"),
    cache_ttl=60,
)
async def load(repos: Repositories, params: MaterialParamsV2) -> list[dict[str, Any]]:
    """Materialstammdaten mit getrennter Werks-ID und berechnetem Bestandswert."""
    repo = await repos.materials()
    raw = await repo.fetch_materials()
    return transform(raw, params)
