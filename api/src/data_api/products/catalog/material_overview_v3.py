"""
material-overview v3 -- same subject, breaking schema change.

Changes against v2, and why each one forces a MAJOR bump:

    plant       -> plant_id + plant_name    field replaced -> breaking
    unit        -> removed                  field removed  -> breaking
    stock_value (new, computed)             field added    -> would be MINOR alone

v2 and v3 sit side by side as separate files, each with its own query. That is
deliberate: v3 may change the Cypher without touching v2.
"""
from __future__ import annotations

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
       p.id        AS plant_id,
       p.name      AS plant_name,
       m.status    AS status,
       m.bestand   AS stock,
       m.preis     AS price,
       m.geaendert AS changed_on
ORDER BY m.nr
"""


# 2. The contract.
class MaterialRowV3(BaseModel):
    material_number: str
    description: str | None = None
    material_group: str | None = None
    plant_id: str | None = Field(None, description="New in v3 -- stable key.")
    plant_name: str | None = None
    status: str | None = None
    stock: int | None = None
    price: float | None = None
    stock_value: float | None = Field(
        None, description="Computed: stock * price. Not stored anywhere."
    )
    changed_on: str | None = None


# 3. The allowed filters.
class MaterialParamsV3(ProductParams):
    status: list[str] | None = None
    plant_id: list[str] | None = Field(None, description="Filters on the id, not the name.")
    material_group: list[str] | None = None
    unclassified_only: bool = False
    search: str | None = None
    min_stock_value: float | None = Field(
        None, ge=0, description="Only positions at or above this stock value."
    )


# 4. The domain logic -- pure, no database, no HTTP.
def transform(rows: list[dict[str, Any]], params: MaterialParamsV3) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    needle = (params.search or "").strip().lower()

    for row in rows:
        material_group = row.get("material_group") or None
        stock = row.get("stock")
        price = row.get("price")
        # None means "unknown", not "zero" -- so the value stays empty rather
        # than being rounded down to 0.
        stock_value = (
            round(float(stock) * float(price), 2)
            if stock is not None and price is not None
            else None
        )

        if params.status and row.get("status") not in params.status:
            continue
        if params.plant_id and row.get("plant_id") not in params.plant_id:
            continue
        if params.material_group and material_group not in params.material_group:
            continue
        if params.unclassified_only and material_group is not None:
            continue
        if params.min_stock_value is not None and (stock_value or 0) < params.min_stock_value:
            continue
        if needle and needle not in f"{row.get('material_number', '')} {row.get('description', '')}".lower():
            continue

        result.append({
            "material_number": row["material_number"],
            "description": row.get("description"),
            "material_group": material_group,
            "plant_id": row.get("plant_id"),
            "plant_name": row.get("plant_name"),
            "status": row.get("status"),
            "stock": int(stock) if stock is not None else None,
            "price": float(price) if price is not None else None,
            "stock_value": stock_value,
            "changed_on": row.get("changed_on"),
        })
    return result


# 5. The wiring.
async def load(sources: Sources, params: MaterialParamsV3) -> list[dict[str, Any]]:
    """Material master data with a separate plant id and a computed stock value."""
    return transform(await sources.neo4j(CYPHER), params)


# 6. Publish.
registry.add(DataProduct(
    name="material-overview",
    version="3.0",
    summary="Material master data including stock value",
    item_model=MaterialRowV3,
    params_model=MaterialParamsV3,
    loader=load,
    owner="team-material-management",
    tags=("material", "master-data"),
    cache_ttl=60,
))
