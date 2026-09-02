"""
material-overview v2 -- the material table of the material management dashboard.

DEPRECATED: v3 supersedes it. This file stays unchanged until the sunset date is
reached and the access logs show nobody requests /v2 any more -- then it is
deleted, and its route, docs and catalog entry disappear with it.

A version still in use is NOT touched. "Let's just patch v2 quickly, only one
dashboard uses it" is the usual way versioning falls apart.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from data_api.db.sources import Sources
from data_api.products.base import DataProduct, ProductParams
from data_api.products.registry import registry

# 1. The query. Graph properties keep their names, the aliases are English.
CYPHER = """
MATCH (m:Material)
OPTIONAL MATCH (m)-[:HAS_WARENGRUPPE]->(g:Warengruppe)
OPTIONAL MATCH (m)-[:LOCATED_IN]->(p:Werk)
RETURN m.nr        AS material_number,
       m.name      AS description,
       g.name      AS material_group,
       p.name      AS plant,
       m.status    AS status,
       m.einheit   AS unit,
       m.bestand   AS stock,
       m.geaendert AS changed_on
ORDER BY m.nr
"""


# 2. The contract: exactly these fields reach the dashboard.
class MaterialRowV2(BaseModel):
    material_number: str = Field(description="Unique material number.")
    description: str | None = None
    material_group: str | None = Field(None, description="Empty/None = unclassified.")
    plant: str | None = None
    status: str | None = Field(None, description="Aktiv | Nicht geliefert | Obsolet | Gesperrt")
    unit: str | None = None
    stock: int | None = None
    changed_on: str | None = None


# 3. The allowed filters.
class MaterialParamsV2(ProductParams):
    status: list[str] | None = Field(None, description="Multi-select (OR-combined).")
    plant: list[str] | None = None
    material_group: list[str] | None = None
    unclassified_only: bool = Field(False, description="Only materials without a group.")
    search: str | None = Field(None, description="Free text in material number or description.")


# 4. The domain logic -- pure, no database, no HTTP.
def transform(rows: list[dict[str, Any]], params: MaterialParamsV2) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    needle = (params.search or "").strip().lower()

    for row in rows:
        material_group = row.get("material_group") or None

        if params.status and row.get("status") not in params.status:
            continue
        if params.plant and row.get("plant") not in params.plant:
            continue
        if params.material_group and material_group not in params.material_group:
            continue
        if params.unclassified_only and material_group is not None:
            continue
        if needle and needle not in f"{row.get('material_number', '')} {row.get('description', '')}".lower():
            continue

        result.append({
            "material_number": row["material_number"],
            "description": row.get("description"),
            "material_group": material_group,
            "plant": row.get("plant"),
            "status": row.get("status"),
            "unit": row.get("unit"),
            "stock": int(row["stock"]) if row.get("stock") is not None else None,
            "changed_on": row.get("changed_on"),
        })
    return result


# 5. The wiring -- deliberately boring.
async def load(sources: Sources, params: MaterialParamsV2) -> list[dict[str, Any]]:
    """Material master data including group and plant."""
    return transform(await sources.neo4j(CYPHER), params)


# 6. Publish.
registry.add(DataProduct(
    name="material-overview",
    version="2.1",
    summary="Material master data for the overview table",
    item_model=MaterialRowV2,
    params_model=MaterialParamsV2,
    loader=load,
    owner="team-material-management",
    tags=("material", "master-data"),
    cache_ttl=60,
    deprecated=True,
    sunset=dt.date(2026, 12, 31),
))
