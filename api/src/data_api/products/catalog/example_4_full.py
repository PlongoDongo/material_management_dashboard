"""
TEMPLATE 4 of 4 -- everything at once: filters, paging and a transform.

    filters      yes, in Cypher
    pagination   SKIP/LIMIT in the query (paginated_by_source=True)
    COUNT query  yes, over the same filter
    transform()  yes -- computes a field the graph does not store

WHEN TO USE THIS
When a big result has to be paged AND the rows need something the database
cannot hand you. It is the most work of the four, so reach for template 2 or 3
first and come here only when both halves are genuinely needed.

THE ONE RULE, AND WHY IT MATTERS MOST HERE
> A transform on a paged product may COMPUTE. It may not FILTER.

The window has already been cut when `transform()` runs, so a filter in there
filters ONE PAGE. `LIMIT 20` fetches 20 rows, the transform drops 17, the client
sees 3 and concludes there is no more data. `meta.total_count` still reports the
unfiltered number, so nothing looks wrong. This is the single most common way
server-side paging goes subtly wrong -- and `tests/test_examples.py` fails the
build if a filtering construct appears in `transform()` here.

If you need a filter on a computed value, it has to move into the query too
(`WHERE m.bestand * m.preis >= $min_value`), or the product goes back to
template 3 where filtering after the fact is safe.

WHY THE QUERY IS BUILT FROM A SHARED FRAGMENT
The page query and the COUNT must apply the IDENTICAL filter. Written out twice
they drift -- somebody adds a condition to one and not the other, and from then
on `total_count` is quietly wrong. So there is one `_MATCH_AND_FILTER` string
and two RETURN clauses glued onto it. They cannot disagree, because there is
only one filter.

The same trick on the value side: one `filters` dict goes to both queries.

THE LADDER
    1  plain        every row, no filters
    2  paged        SKIP/LIMIT + COUNT in the query
    3  filtered     WHERE clauses, router does the paging
    4  full         <- you are here
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from data_api.db.sources import Sources
from data_api.products.base import DataProduct, Page, ProductParams
from data_api.products.registry import registry

# 1a. THE SHARED HALF: what is matched and how it is filtered. Both queries
# below start from this string, which is what stops them from drifting apart.
_MATCH_AND_FILTER = """
MATCH (m:Material)
WHERE ($status    IS NULL OR m.status  IN $status)
  AND ($min_stock IS NULL OR m.bestand >= $min_stock)
"""

# 1b. THE PAGE. ORDER BY is interpolated, not parameterised -- Cypher does not
# allow a parameter in that position. That is only safe because the value comes
# out of `_SORTS` below and never from the request.
CYPHER_PAGE = _MATCH_AND_FILTER + """
RETURN m.nr      AS material_number,
       m.name    AS description,
       m.status  AS status,
       m.bestand AS stock,
       m.preis   AS price
ORDER BY {order_by}
SKIP $offset LIMIT $limit
"""

# 1c. THE COUNT, over the same filter.
CYPHER_COUNT = _MATCH_AND_FILTER + """
RETURN count(m) AS total
"""

# The sort whitelist. A property name cannot be a Cypher parameter, so a
# client-chosen sort column has to be interpolated -- and `ORDER BY` is a
# favourite place for injection precisely because it looks harmless. Map a
# CLOSED set of names onto fixed fragments and never format a request value into
# a query.
#
# Two guards on purpose: the `Literal` on the params model rejects anything else
# with a 422 and documents the API, and this dict is what actually reaches the
# database.
_SORTS: dict[str, str] = {
    "material_number": "m.nr",
    # Non-unique columns get m.nr as a tie-breaker, or rows shuffle between pages.
    "stock": "m.bestand DESC, m.nr",
}


# 2. THE CONTRACT. `stock_value` and `stock_class` are computed -- they exist in
# no database, which is exactly why this product needs a transform.
class ExampleFullRow(BaseModel):
    material_number: str
    description: str | None = None
    status: str | None = None
    stock: int | None = None
    price: float | None = None
    stock_value: float | None = Field(None, description="Computed: stock * price.")
    stock_class: str = Field("unknown", description="unknown | low | medium | high")


# 3. THE FILTERS. Every field except `sort` appears as a `$name` in the query
# above; `sort` is not a value but a piece of the query itself.
class ExampleFullParams(ProductParams):
    status: list[str] | None = Field(None, description="Only these status values.")
    min_stock: int | None = Field(None, ge=0, description="Only at or above this stock.")
    sort: Literal["material_number", "stock"] = "material_number"


# 4. THE BUSINESS LOGIC. Pure: rows in, rows out. No database, no HTTP, no
# `params` -- which is what makes it testable without any infrastructure, and
# what keeps it from filtering.
def transform(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds the computed fields. Computes only -- never drops a row.

    Dropping one here would filter a single page; see the rule at the top.
    Note the None handling: an unknown stock or price makes the value unknown,
    not zero. Rounding "we do not know" down to 0 puts a wrong number in a sum
    that nobody questions.
    """
    for row in rows:
        stock, price = row.get("stock"), row.get("price")
        value = round(stock * price, 2) if stock is not None and price is not None else None
        row["stock_value"] = value
        row["stock_class"] = _stock_class(value)
    return rows


def _stock_class(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 500_000:
        return "high"
    if value >= 50_000:
        return "medium"
    return "low"


# 5. THE LOADER. Keep this boring: build the arguments, run the two queries,
# hand the rows to `transform`.
async def load(sources: Sources, params: ExampleFullParams) -> Page:
    """One page of materials with their computed stock value."""
    # Two exclusions: `sort` is a piece of the query rather than a value, and
    # the window belongs to the page query only -- the COUNT counts everything
    # that matches.
    filters = params.model_dump(exclude={"sort", "limit", "offset"})

    records = await sources.neo4j(
        CYPHER_PAGE.format(order_by=_SORTS[params.sort]),
        offset=params.offset,
        limit=params.limit,
        **filters,
    )
    counted = await sources.neo4j(CYPHER_COUNT, **filters)

    return Page(rows=transform(records), total=counted[0]["total"] if counted else 0)


# 6. PUBLISH.
registry.add(DataProduct(
    name="example-4-full",
    version="1.0",
    summary="TEMPLATE 4: filters and paging in the query, plus a transform",
    description=(
        "The full shape: WHERE clauses and SKIP/LIMIT in Cypher, a COUNT for the "
        "total, and a pure `transform()` that adds computed fields. The transform "
        "may compute but must never filter -- it runs after the window was cut."
    ),
    item_model=ExampleFullRow,
    params_model=ExampleFullParams,
    loader=load,
    owner="team-material-management",
    tags=("example", "template", "paged", "filtered"),
    cache_ttl=30,
    paginated_by_source=True,
))
