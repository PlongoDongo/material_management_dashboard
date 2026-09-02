"""
supplier-risk v2 -- two sources, real computation.

This product shows what the API layer is for. It combines
    master data from Neo4j    (which supplier delivers how many materials)
with movement data from Postgres (on-time rate, complaints)
and derives a risk score from both.

Doing this in a dashboard would mean every dashboard knows both connections,
holds both sets of credentials and copies the score formula. The first time the
formula changes, two dashboards show two different numbers -- and nobody knows
which one is right.

The computation runs in Polars: the same library the dashboards already use,
column-oriented and much faster than looping over dicts.

v1 (German field names) has been removed as part of the switch to English.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from data_api.db.sources import Sources
from data_api.products.base import DataProduct, ProductParams
from data_api.products.registry import registry

# 1a. Master data from the graph.
#
# `$country` is a PARAMETER, not spliced-in text. The line
#     WHERE $country IS NULL OR s.land IN $country
# is the standard idiom for an OPTIONAL filter: pass nothing and the condition
# disappears, pass something and it applies. No second query, no string building.
#
# Values, lists, SKIP and LIMIT can be parameterised. Labels, relationship types
# and property names cannot -- they are part of the query structure.
CYPHER = """
MATCH (s:Lieferant)-[:SUPPLIES]->(m:Material)
WHERE $country IS NULL OR s.land IN $country
RETURN s.id     AS supplier_id,
       s.name   AS supplier_name,
       s.land   AS country,
       count(m) AS material_count
ORDER BY s.id
"""

# 1b. Movement data from Postgres. `:since` and `:ids` are passed as parameters,
#     not spliced into the text (SQL injection).
#
#     `:ids` are the suppliers the graph query left over. Without that
#     restriction the SQL would read the entire delivery history and the join
#     would throw most of it away -- leaving the expensive side unfiltered, the
#     very side that grows over time.
SQL = """
SELECT supplier_id, material_number, delivered_on, promised_on, quantity, complaints
FROM   deliveries
WHERE  delivered_on >= :since
  AND  supplier_id = ANY(:ids)
ORDER  BY supplier_id, delivered_on
"""


# 2. The contract.
class SupplierRiskRow(BaseModel):
    supplier_id: str
    supplier_name: str | None = None
    country: str | None = None
    material_count: int = 0
    deliveries: int = Field(0, description="Deliveries considered in the time window.")
    # Every metric is None when there was no delivery in the window. "We do not
    # know" is a different statement from "uncritical", and a risk product has
    # to be able to express the difference.
    on_time_rate_pct: float | None = Field(None, description="Share of on-time deliveries in %.")
    avg_delay_days: float | None = None
    complaint_rate_pct: float | None = None
    risk_score: float | None = Field(
        None, description="0 (uncritical) to 100 (critical). None = no data."
    )
    risk_class: str = Field("unknown", description="unknown | low | medium | high")


# 3. The allowed filters.
class SupplierRiskParams(ProductParams):
    since: dt.date = Field(dt.date(2026, 1, 1), description="Start of the evaluation window.")
    tolerance_days: int = Field(
        0, ge=0, le=30, description="A delay of up to X days still counts as on time."
    )
    min_deliveries: int = Field(
        1, ge=0, description="Suppliers with fewer deliveries are hidden."
    )
    risk_class: list[str] | None = None
    # This filter is pushed down to the database (see CYPHER above) rather than
    # applied afterwards in Python -- it shrinks the result inside the graph.
    country: list[str] | None = Field(None, description="Only suppliers from these countries.")


# Weights of the score formula. Named constants on purpose rather than scattered
# through the code: this is the knob people argue about. Change one and the
# MEANING of risk_score changes -- that is a breaking change and needs a new
# major version.
WEIGHT_DELAY = 0.5
WEIGHT_ON_TIME = 0.3
WEIGHT_COMPLAINTS = 0.2


def _risk_class(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


# 4. The domain logic -- pure, no database, no HTTP.
def transform(
    suppliers: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    params: SupplierRiskParams,
) -> list[dict[str, Any]]:
    if not suppliers:
        return []

    master = pl.DataFrame(
        suppliers,
        schema={"supplier_id": pl.Utf8, "supplier_name": pl.Utf8,
                "country": pl.Utf8, "material_count": pl.Int64},
    )

    if deliveries:
        metrics = (
            pl.DataFrame(deliveries)
            .with_columns(delay_days=(pl.col("delivered_on") - pl.col("promised_on")).dt.total_days())
            .group_by("supplier_id")
            .agg(
                deliveries=pl.len(),
                avg_delay_days=pl.col("delay_days").mean(),
                on_time=(pl.col("delay_days") <= params.tolerance_days).mean(),
                complaint_rate=(pl.col("complaints") > 0).mean(),
            )
        )
    else:
        metrics = pl.DataFrame(
            schema={"supplier_id": pl.Utf8, "deliveries": pl.UInt32,
                    "avg_delay_days": pl.Float64, "on_time": pl.Float64,
                    "complaint_rate": pl.Float64},
        )

    frame = (
        master.join(metrics, on="supplier_id", how="left")
        .with_columns(
            deliveries=pl.col("deliveries").fill_null(0).cast(pl.Int64),
            avg_delay_days=pl.col("avg_delay_days").fill_null(0.0),
            on_time=pl.col("on_time").fill_null(1.0),
            complaint_rate=pl.col("complaint_rate").fill_null(0.0),
        )
        .with_columns(
            # Three normalised parts. The delay is capped at 14 days -- beyond
            # that "very bad" can no longer be meaningfully differentiated.
            risk_score=(
                WEIGHT_DELAY * (pl.col("avg_delay_days").clip(0, 14) / 14 * 100)
                + WEIGHT_ON_TIME * ((1 - pl.col("on_time")) * 100)
                + WEIGHT_COMPLAINTS * (pl.col("complaint_rate") * 100)
            ).round(1)
        )
        .with_columns(
            on_time_rate_pct=(pl.col("on_time") * 100).round(1),
            complaint_rate_pct=(pl.col("complaint_rate") * 100).round(1),
            avg_delay_days=pl.col("avg_delay_days").round(2),
        )
        .filter(pl.col("deliveries") >= params.min_deliveries)
        # nulls_last: suppliers without data end up at the bottom, but labelled
        # "unknown" rather than "low risk".
        .sort("risk_score", descending=True, nulls_last=True)
    )

    rows = frame.to_dicts()
    for row in rows:
        row.pop("on_time", None)
        row.pop("complaint_rate", None)

        if row["deliveries"] == 0:
            # No delivery in the window means no data. The fill_null defaults
            # above (100% on time, zero delay) would otherwise produce
            # risk_score = 0.0 and therefore "low" -- a supplier with no history
            # at all would sit at the bottom of the risk list.
            for metric in ("risk_score", "on_time_rate_pct",
                           "avg_delay_days", "complaint_rate_pct"):
                row[metric] = None

        row["risk_class"] = _risk_class(row["risk_score"])

    if params.risk_class:
        rows = [r for r in rows if r["risk_class"] in params.risk_class]
    return rows


# 5. The wiring -- this is where both sources become visible.
async def load(sources: Sources, params: SupplierRiskParams) -> list[dict[str, Any]]:
    """Joins supplier master data with the delivery history and scores the risk."""
    # Parameters are passed by name. Never splice them into the query text --
    # that would be an injection hole and would stop the database reusing the
    # query plan.
    suppliers = await sources.neo4j(CYPHER, country=params.country)
    if not suppliers:
        return []          # no supplier left -> skip the SQL query entirely

    # The filter reaches BOTH sources: the ids from the graph narrow the
    # delivery history instead of loading it in full and joining afterwards.
    ids = [row["supplier_id"] for row in suppliers]
    deliveries = await sources.postgres(SQL, since=params.since, ids=ids)
    return transform(suppliers, deliveries, params)


# 6. Publish.
registry.add(DataProduct(
    name="supplier-risk",
    version="2.0",
    summary="Supplier risk from master data (Neo4j) and delivery reliability (Postgres)",
    item_model=SupplierRiskRow,
    params_model=SupplierRiskParams,
    loader=load,
    owner="team-supply-chain",
    tags=("supplier", "risk", "cross-source"),
    cache_ttl=300,   # expensive computation, data changes daily
))
