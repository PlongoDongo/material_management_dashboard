"""
TEMPLATE 3 of 4 -- filters in the query, no paging in the query.

The client may narrow the result, the database does the narrowing, and the
router still cuts the page out of whatever comes back.

    filters      yes, in Cypher
    pagination   the router slices the finished list
    COUNT query  no -- the loader returns everything that matched, so
                 `len(rows)` IS the total and the router fills it in
    transform()  no

WHEN TO USE THIS
When filtering shrinks the result to something that comfortably fits in one
response. That is the common case: a status filter turns 500 000 materials into
900, and 900 rows need no paging. You get the index usage of a database-side
filter without the second query and the extra cache entries that paging costs.

THE ONE RULE
> Filters and `LIMIT` go to the database together, or neither.

This template deliberately takes the "neither" half: filters in Cypher, window
in the router. That combination is safe because the query returns EVERY matching
row -- the router then slices a complete result, exactly as in template 1.

What is not safe is the mix: `LIMIT` in the query while a filter still runs in
Python afterwards. Then the filter runs on one page instead of the whole result,
a search that should find 40 matches finds three, and nothing errors. If you
want both, use template 4, which does both properly.

THE OPTIONAL-FILTER IDIOM
Every condition reads `$x IS NULL OR ...`. Pass nothing and the condition
disappears; pass something and it applies. One query serves all 2^n filter
combinations and the database can reuse its plan for each of them -- no string
building, no second query, no `if` cascade.

Declaring list filters as `list[X] | None` matters: `ProductParams` turns an
empty list into `None`, so an empty multi-select in a dashboard means "no
filter" instead of "match nothing".

THE LADDER
    1  plain        every row, no filters
    2  paged        SKIP/LIMIT + COUNT in the query
    3  filtered     <- you are here
    4  full         filters + paging + a transform of your own
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from db.sources import Sources
from products.base import DataProduct, ProductParams
from products.registry import registry

# 1. THE QUERY. Note there is no SKIP/LIMIT: this product hands back everything
# that matched and lets the router take the window.
CYPHER = """
MATCH (m:Material)
WHERE ($status    IS NULL OR m.status  IN $status)
  AND ($min_stock IS NULL OR m.bestand >= $min_stock)
RETURN m.nr      AS material_number,
       m.name    AS description,
       m.status  AS status,
       m.bestand AS stock
ORDER BY m.nr
"""


# 2. THE CONTRACT.
class ExampleFilteredRow(BaseModel):
    material_number: str
    description: str | None = None
    status: str | None = None
    stock: int | None = None


# 3. THE FILTERS. Every field here appears as a `$name` in the query above --
# that is the rule this template lives by. A field declared here but missing
# from the WHERE clause is simply ignored, silently.
#
# Name them exactly like the Cypher parameters and the loader can pass the whole
# object through without copying anything field by field.
class ExampleFilteredParams(ProductParams):
    status: list[str] | None = Field(None, description="Only these status values.")
    min_stock: int | None = Field(None, ge=0, description="Only at or above this stock.")


# 4. THE LOADER.
async def load(sources: Sources, params: ExampleFilteredParams) -> list[dict[str, Any]]:
    """Every material that matches the filters.

    `limit`/`offset` are excluded because this query has no window -- passing
    parameters a query never references is harmless but misleading. `sort` is
    not declared here at all; see template 4 for how to offer one safely.
    """
    return await sources.neo4j(CYPHER, **params.model_dump(exclude={"limit", "offset"}))


# 5. PUBLISH.
registry.add(DataProduct(
    name="example-3-filtered",
    version="1.0",
    summary="TEMPLATE 3: filters in the query, router does the paging",
    description=(
        "Filtering happens in Cypher, so the database can use its indexes, but "
        "the query returns every match and the router slices. Copy this when a "
        "filter shrinks the result to something that fits in one response."
    ),
    item_model=ExampleFilteredRow,
    params_model=ExampleFilteredParams,
    loader=load,
    tags=("example", "template", "filtered"),
    cache_ttl=60,
))
