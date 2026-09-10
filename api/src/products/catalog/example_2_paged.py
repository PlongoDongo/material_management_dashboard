"""
TEMPLATE 2 of 4 -- paging inside the database, still no filters.

Same shape as template 1, plus the three things that server-side paging needs:
SKIP/LIMIT in the query, a stable ORDER BY, and a COUNT so the client learns how
many rows there are in total.

    filters      no
    pagination   SKIP/LIMIT in the query (paginated_by_source=True)
    COUNT query  yes
    transform()  no

WHEN TO USE THIS INSTEAD OF TEMPLATE 1
When the full set does not comfortably fit in one response. Template 1 fetches
everything and throws most of it away; this one asks the database for exactly
the window the client wants. At 64 rows that is a waste of a second query, at
two million it is the difference between working and not.

THE THREE THINGS THAT MOVE TOGETHER
Leave one out and the product returns wrong numbers with status 200:

  1. `paginated_by_source=True` on the DataProduct, so the router stops slicing.
     Without it the query returns rows 101-200 and the router then takes rows
     101-200 OF THOSE -- page 1 looks right, every later page is empty.
  2. ORDER BY on something UNIQUE. SKIP/LIMIT over a partial order is undefined:
     a row may show up on two pages and another on none. `m.nr` is unique, so it
     is a total order. A non-unique column needs a tie-breaker
     (`ORDER BY m.bestand DESC, m.nr`).
  3. A COUNT over the same rows, because `len(rows)` is now the page size, not
     the total. The loader returns a `Page`, which is just those two values with
     names on them.

`limit`/`offset` land in the cache key automatically -- the router derives that
from the same flag, so page 2 can never be served page 1's rows.

Nothing here needs a params model: `limit` and `offset` come from
`ProductParams`, and this product has no filters of its own.

THE LADDER
    1  plain        every row, no query-side paging
    2  paged        <- you are here
    3  filtered     adds WHERE clauses driven by query parameters
    4  full         filters + paging + a transform of your own
"""
from __future__ import annotations

from pydantic import BaseModel

from db.sources import Sources
from products.base import DataProduct, Page, ProductParams
from products.registry import registry

# 1a. THE PAGE QUERY. `$offset` and `$limit` are ordinary Cypher parameters,
# named exactly like the fields on ProductParams -- that is what lets the loader
# below pass them through without any copying.
CYPHER_PAGE = """
MATCH (m:Material)
RETURN m.nr      AS material_number,
       m.name    AS description,
       m.status  AS status,
       m.bestand AS stock
ORDER BY m.nr
SKIP $offset LIMIT $limit
"""

# 1b. THE COUNT. No ORDER BY and no window -- sorting rows you are about to
# count is pure cost. With no filters in play there is nothing that could drift
# between the two queries; template 3 shows what to do once there is.
CYPHER_COUNT = """
MATCH (m:Material)
RETURN count(m) AS total
"""


# 2. THE CONTRACT.
class ExamplePagedRow(BaseModel):
    material_number: str
    description: str | None = None
    status: str | None = None
    stock: int | None = None


# 3. THE LOADER. Returns a `Page` rather than a list, because there are now two
# answers: the window, and how many rows it was cut from.
async def load(sources: Sources, params: ProductParams) -> Page:
    """One page of materials, ordered by material number."""
    records = await sources.neo4j(CYPHER_PAGE, offset=params.offset, limit=params.limit)
    # Runs even when the page is empty: "nothing on page 40 of 3" and "nothing
    # at all" are different answers, and only the total tells them apart.
    counted = await sources.neo4j(CYPHER_COUNT)
    return Page(rows=records, total=counted[0]["total"] if counted else 0)


# 4. PUBLISH.
registry.add(DataProduct(
    name="example-2-paged",
    version="1.0",
    summary="TEMPLATE 2: paging in the query, no filters",
    description=(
        "Server-side pagination without filters. The query returns exactly the "
        "requested window and a second query counts the total. Copy this when "
        "the full set is too big for one response."
    ),
    item_model=ExamplePagedRow,
    loader=load,
    owner="team-material-management",
    tags=("example", "template", "paged"),
    # Short on purpose: with the window in the cache key there is one entry per
    # page, so a long TTL fills the cache with stale single pages.
    cache_ttl=30,
    paginated_by_source=True,
))
