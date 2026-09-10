"""
TEMPLATE 1 of 4 -- the smallest data product there is.

Query, contract, publish. Nothing else. If your product does not need filters,
paging or computed fields, this is the whole file -- copy it and change three
things.

    filters      no
    pagination   the router slices the finished list
    COUNT query  no
    transform()  no

WHEN THIS IS ENOUGH
Small, complete reference data that a client wants whole: plants, unit codes,
status values, a lookup table. "Small" means it comfortably fits in one
response; see the ceiling note below.

WHAT YOU STILL GET FOR FREE
`limit` and `offset` exist on every product whether you declare them or not --
they come from `ProductParams`, and the router applies them AFTER the loader
returns. So this product answers 1000 rows by default and up to 50 000 on
request, without a line of code here. `meta.total_count` reports how many there
really were, so a client can tell it was truncated.

If the full set is bigger than that ceiling, you are no longer in template 1.
Go to template 2 (paging in the query) -- or raise the ceiling deliberately,
which is one line in a params model (see the developer guide, §6).

THE LADDER
    1  plain        <- you are here
    2  paged        adds SKIP/LIMIT + COUNT in the query
    3  filtered     adds WHERE clauses driven by query parameters
    4  full         filters + paging + a transform of your own
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from db.sources import Sources
from products.base import DataProduct
from products.registry import registry

# 1. THE QUERY. Alias every column to the name the contract uses -- `m.nr AS
# material_number` -- and there is nothing left to map afterwards.
#
# ORDER BY is not required here (nothing is being paged inside the database),
# but a stable order makes responses comparable between calls and costs nothing
# at this size.
CYPHER = """
MATCH (m:Material)
RETURN m.nr      AS material_number,
       m.name    AS description,
       m.status  AS status,
       m.bestand AS stock
ORDER BY m.nr
"""


# 2. THE CONTRACT. This is what consumers may rely on, and the only thing that
# makes a version breaking when it changes. FastAPI validates every row against
# it on the way out, which is also where `stock` gets its int cast.
class ExamplePlainRow(BaseModel):
    material_number: str
    description: str | None = None
    status: str | None = None
    stock: int | None = None


# 3. THE LOADER. One line: ask, return. The records already have the contract's
# field names, so they ARE the rows.
async def load(sources: Sources, params: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Every material, unfiltered.

    `params` is unused and typed loosely on purpose: this product declares no
    parameters of its own, so it gets the plain `ProductParams` with nothing but
    limit/offset on it.
    """
    return await sources.neo4j(CYPHER)


# 4. PUBLISH. This is the line that makes the route appear -- there is no
# router file to edit and no import list to maintain.
registry.add(DataProduct(
    name="example-1-plain",
    version="1.0",
    summary="TEMPLATE 1: every row, no filters, no paging in the query",
    description=(
        "Smallest possible data product: query, contract, publish. Copy this "
        "when a client wants a small reference set whole. `limit`/`offset` still "
        "work -- the router applies them to the finished list."
    ),
    item_model=ExamplePlainRow,
    loader=load,
    owner="team-material-management",
    tags=("example", "template"),
    cache_ttl=60,
))
