"""
material-search v1 -- the worked example of FULL filter pushdown.

Every other product in this catalog fetches its rows and narrows them in Python.
This one does the opposite: filtering, sorting and the window all happen inside
Neo4j, and Python only maps the result onto the contract. It exists to show how
that is done correctly, because doing it HALFWAY is worse than not doing it.

WHEN TO COPY THIS INSTEAD OF material_overview_v3
=================================================
Use this shape when a client really pages -- a server-side table, infinite
scroll -- or when the result outgrows ProductParams.limit (50k). Use the
material_overview shape when a dashboard pulls the whole set once and filters it
in the browser, which is what our Dash app does today. Pushdown is not free: it
costs a second COUNT query and one cache entry per page instead of one per
filter combination.

THE FIVE THINGS THAT HAVE TO MOVE TOGETHER
==========================================
Pushdown is all-or-nothing per product. Leave one of these out and the product
reports wrong numbers with status 200:

  1. EVERY filter in the query. One filter left in Python runs on the
     already-truncated page: LIMIT 20 fetches 20 rows, Python drops 17, the
     client sees 3 and concludes there is no more data.
  2. ORDER BY in the query, on something UNIQUE. SKIP/LIMIT without a total
     order is undefined -- the database may return a row on page 1 and again on
     page 2, and drop another entirely.
  3. SKIP/LIMIT in the query, and NOT in the router. That is what
     `paginated_by_source=True` below switches off.
  4. A COUNT query for meta.total_count, over the SAME filter. `len(rows)` is
     now the page size, not the total.
  5. limit/offset in the cache key. The router derives that from the flag, so
     it cannot be forgotten here.

WHY THE QUERY IS BUILT FROM A SHARED FRAGMENT
=============================================
Point 4 is where this design rots if you let it. Two queries that must apply the
identical filter, written out twice, WILL drift -- someone adds a filter to the
page query and not to the count, and from then on total_count is quietly wrong.
So there is one `_MATCH_AND_FILTER` string and two RETURN clauses glued onto it.
The queries cannot disagree because there is only one filter.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from data_api.db.sources import Sources
from data_api.products.base import DataProduct, Page, ProductParams
from data_api.products.registry import registry

# 1a. The shared half: what is matched and how it is filtered.
#
# Every condition follows the `$x IS NULL OR ...` idiom -- pass nothing and the
# condition disappears, pass something and it applies. One query serves all 2^5
# filter combinations, and Neo4j can reuse the plan for each of them.
#
# `toLower()` on both sides for the search: Cypher's CONTAINS is
# case-sensitive, so without it "sensor" would not find "Sensorhalter". A real
# deployment would put a full-text index on this instead -- CONTAINS cannot use
# a plain property index and stays a scan.
_MATCH_AND_FILTER = """
MATCH (m:Material)
OPTIONAL MATCH (m)-[:HAS_WARENGRUPPE]->(g:Warengruppe)
OPTIONAL MATCH (m)-[:LOCATED_IN]->(p:Werk)
WITH m, g, p
WHERE ($status           IS NULL OR m.status IN $status)
  AND ($plant_id         IS NULL OR p.id     IN $plant_id)
  AND ($material_group   IS NULL OR g.name   IN $material_group)
  AND ($unclassified_only = false OR g IS NULL)
  AND ($min_stock         IS NULL OR m.bestand >= $min_stock)
  AND ($search           IS NULL OR toLower(m.nr)   CONTAINS $search
                                 OR toLower(m.name) CONTAINS $search)
"""

# 1b. The page. ORDER BY m.nr is not decoration: SKIP/LIMIT over an unordered
# result is undefined, and m.nr is unique, so this is a TOTAL order. Sorting by
# something non-unique (status, say) would need a tie-breaker -- `ORDER BY
# m.status, m.nr` -- or rows would shuffle between pages.
#
# $skip and $limit are ordinary parameters. Values may be parameterised in
# Cypher; labels, relationship types and property names may not -- which is why
# the sort column below goes through a whitelist instead.
CYPHER_PAGE = _MATCH_AND_FILTER + """
RETURN m.nr        AS material_number,
       m.name      AS description,
       g.name      AS material_group,
       p.id        AS plant_id,
       p.name      AS plant_name,
       m.status    AS status,
       m.bestand   AS stock,
       m.geaendert AS changed_on
ORDER BY {order_by}
SKIP $skip LIMIT $limit
"""

# 1c. The count, over the SAME filter. No ORDER BY, no window -- sorting rows
# you are about to count is pure cost.
CYPHER_COUNT = _MATCH_AND_FILTER + """
RETURN count(m) AS total
"""

# The sort whitelist. A property name cannot be a Cypher parameter, so the only
# safe way to let a client choose one is to map a CLOSED set of names onto fixed
# fragments. Never f-string a client value into a query -- that is injection,
# and `ORDER BY` is a favourite place for it because it looks harmless.
#
# Pydantic already rejects anything outside the Literal below with a 422, so
# this dict can never be indexed with an unknown key. Two guards, on purpose:
# the Literal documents the API, the dict is what actually reaches the database.
_SORTS: dict[str, str] = {
    "material_number": "m.nr",
    # Non-unique columns get m.nr as a tie-breaker so paging stays stable.
    "stock": "m.bestand DESC, m.nr",
    "changed_on": "m.geaendert DESC, m.nr",
}


# 2. The contract.
class MaterialSearchRow(BaseModel):
    material_number: str
    description: str | None = None
    material_group: str | None = None
    plant_id: str | None = None
    plant_name: str | None = None
    status: str | None = None
    stock: int | None = None
    changed_on: str | None = None


# 3. The allowed filters. EVERY one of these appears in _MATCH_AND_FILTER above
# -- that is the rule this product lives by. Adding a field here without adding
# it to the query is the bug this file exists to prevent.
class MaterialSearchParams(ProductParams):
    status: list[str] | None = None
    plant_id: list[str] | None = None
    material_group: list[str] | None = None
    unclassified_only: bool = False
    min_stock: int | None = Field(None, ge=0, description="Only positions at or above this stock.")
    search: str | None = Field(None, description="Substring of material number or description.")
    sort: Literal["material_number", "stock", "changed_on"] = "material_number"


# 4. There is no `transform()` here, and that is the point.
#
# material_overview_v3 has a 40-line transform that filters and computes. This
# product has none: everything it could filter on, the database already did.
# What is left is renaming and type coercion, which is what `_row` does.
def _row(record: dict[str, Any]) -> dict[str, Any]:
    """One graph record -> one contract row. No filtering, no arithmetic."""
    stock = record.get("stock")
    return {
        "material_number": record["material_number"],
        "description": record.get("description"),
        # "" and None both mean "unclassified" in the graph; the contract says None.
        "material_group": record.get("material_group") or None,
        "plant_id": record.get("plant_id"),
        "plant_name": record.get("plant_name"),
        "status": record.get("status"),
        "stock": int(stock) if stock is not None else None,
        "changed_on": record.get("changed_on"),
    }


def _filter_arguments(params: MaterialSearchParams) -> dict[str, Any]:
    """The filter parameters -- built ONCE and passed to both queries.

    The other half of the anti-drift measure: the shared query fragment cannot
    disagree about the conditions, and this cannot disagree about the values.

    `search` is lowered here rather than in the query so `toLower($search)` does
    not run per row.
    """
    return {
        "status": params.status,
        "plant_id": params.plant_id,
        "material_group": params.material_group,
        "unclassified_only": params.unclassified_only,
        "min_stock": params.min_stock,
        "search": params.search.strip().lower() if params.search else None,
    }


# 5. The wiring. Returns a `Page`, not a list -- see products/base.py.
async def load(sources: Sources, params: MaterialSearchParams) -> Page:
    """One page of materials, filtered and ordered inside the graph."""
    arguments = _filter_arguments(params)

    # The sort fragment is interpolated, not parameterised -- Cypher does not
    # allow a parameter there. Safe only because the value came out of _SORTS,
    # never from the request.
    page_query = CYPHER_PAGE.format(order_by=_SORTS[params.sort])

    records = await sources.neo4j(
        page_query, skip=params.offset, limit=params.limit, **arguments
    )
    # The COUNT runs even when the page is empty: "no rows on page 40 of 3" and
    # "no rows at all" are different answers, and the client needs the total to
    # tell them apart.
    counted = await sources.neo4j(CYPHER_COUNT, **arguments)
    total = counted[0]["total"] if counted else 0

    return Page(rows=[_row(record) for record in records], total=total)


# 6. Publish.
registry.add(DataProduct(
    name="material-search",
    version="1.0",
    summary="Paged material search -- filtered, sorted and windowed in the graph",
    description=(
        "Server-side pagination. Unlike `material-overview`, this product pushes "
        "every filter into Cypher and returns only the requested window, so "
        "`limit`/`offset` reach the database instead of a Python slice. Use it for "
        "table widgets that page; use `material-overview` when a client wants the "
        "whole set at once."
    ),
    item_model=MaterialSearchRow,
    params_model=MaterialSearchParams,
    loader=load,
    owner="team-material-management",
    tags=("material", "search", "paged"),
    # Short: a page is cheap to re-fetch, and with the window in the cache key
    # there are many more entries than for an unpaged product. A long TTL would
    # fill the 512-entry cache with stale single pages.
    cache_ttl=30,
    paginated_by_source=True,
))
