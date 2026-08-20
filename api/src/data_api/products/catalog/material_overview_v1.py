"""
material-overview v1 -- die Materialtabelle des Material-Management-Dashboards.

Das einfachste Muster: eine Cypher-Abfrage, leichte Formung, fertig.
Entspricht dem, was heute `data/repository.py` im Dashboard direkt macht.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from data_api.db.repositories import Repositories
from data_api.products.base import ProductParams
from data_api.products.registry import data_product


class MaterialRowV1(BaseModel):
    """Eine Zeile der Materialuebersicht. DAS ist der Vertrag mit dem Dashboard."""

    material_nr: str = Field(description="Eindeutige Materialnummer.")
    bezeichnung: str | None = None
    warengruppe: str | None = Field(None, description="Leer/None = ohne Klassifizierung.")
    werk: str | None = None
    status: str | None = Field(None, description="Aktiv | Nicht geliefert | Obsolet | Gesperrt")
    einheit: str | None = None
    bestand: int | None = None
    geaendert: str | None = None


class MaterialParamsV1(ProductParams):
    """Serverseitige Filter.

    Faustregel: filtere so frueh wie moeglich. Was hier ankommt, sollte
    idealerweise in die Query wandern (WHERE in Cypher), nicht erst nach dem
    Laden von 2 Mio. Zeilen in Python. Fuer den Anfang filtern wir nach dem
    Laden -- der Umbau ist lokal auf diese Datei begrenzt.
    """

    status: list[str] | None = Field(None, description="Mehrfachauswahl (ODER-verknuepft).")
    werk: list[str] | None = None
    warengruppe: list[str] | None = None
    ohne_klassifizierung: bool = Field(False, description="Nur Materialien ohne Warengruppe.")
    suche: str | None = Field(None, description="Freitext in Material-Nr. oder Bezeichnung.")


def transform(rows: list[dict[str, Any]], params: MaterialParamsV1) -> list[dict[str, Any]]:
    """Reine Funktion: Rohzeilen -> Produktzeilen. Ohne DB testbar.

    Dass diese Funktion weder `Repositories` noch `Request` sieht, ist kein
    Zufall: die fachliche Logik ist der Teil, der Fehler enthaelt, und sie muss
    in Millisekunden testbar sein.
    """
    result: list[dict[str, Any]] = []
    needle = (params.suche or "").strip().lower()

    for row in rows:
        warengruppe = row.get("warengruppe") or None

        if params.status and row.get("status") not in params.status:
            continue
        if params.werk and row.get("werk_name") not in params.werk:
            continue
        if params.warengruppe and warengruppe not in params.warengruppe:
            continue
        if params.ohne_klassifizierung and warengruppe is not None:
            continue
        if needle:
            haystack = f"{row.get('material_nr', '')} {row.get('bezeichnung', '')}".lower()
            if needle not in haystack:
                continue

        result.append({
            "material_nr": row["material_nr"],
            "bezeichnung": row.get("bezeichnung"),
            "warengruppe": warengruppe,
            # v1 kennt nur den Werksnamen -- v2 trennt ID und Name (brechend).
            "werk": row.get("werk_name"),
            "status": row.get("status"),
            "einheit": row.get("einheit"),
            "bestand": int(row["bestand"]) if row.get("bestand") is not None else None,
            "geaendert": row.get("geaendert"),
        })
    return result


@data_product(
    name="material-overview",
    version="1.2",
    summary="Materialstammdaten fuer die Uebersichtstabelle",
    item_model=MaterialRowV1,
    params_model=MaterialParamsV1,
    owner="team-material-management",
    tags=("material", "stammdaten"),
    cache_ttl=60,
    deprecated=True,          # v2 ist da -- v1 bleibt bis zur Migration erreichbar
    sunset=__import__("datetime").date(2026, 12, 31),
)
async def load(repos: Repositories, params: MaterialParamsV1) -> list[dict[str, Any]]:
    """Materialstammdaten inkl. Warengruppe und Werk."""
    repo = await repos.materials()
    raw = await repo.fetch_materials()
    return transform(raw, params)
