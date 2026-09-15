"""
What is a data product?

A data product is a named, versioned dataset -- not simply "a route that
happens to query the database".

This file contains four things:

    ProductParams     base class for a product's allowed query parameters
    ProductMeta       the metadata every response carries
    ProductEnvelope   the response format: {"meta": {...}, "data": [...]}
    DataProduct       the description of a product (name, version, loader, ...)
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductParams(BaseModel):
    """Base class of all parameter models. Every product inherits from it.

    `extra="forbid"` means an unknown query parameter is an error. If a
    dashboard sends `?limmit=10` it gets a 422 instead of silently receiving
    unfiltered data. That is the difference between "caught in a test" and
    "caught in a management meeting".
    """

    model_config = ConfigDict(extra="forbid")

    # limit/offset paginate the FINISHED product response and are applied by the
    # router -- NOT in the query. Two things go wrong if SKIP/LIMIT also appear
    # in the Cypher, and only the first one is obvious:
    #
    #   1. Double slicing. `SKIP 100 LIMIT 100` returns rows 101-200, and the
    #      router then takes rows[100:200] OF THOSE -- empty. Page 1 looks
    #      correct, every later page is silently empty.
    #   2. Short pages. Any filter still applied AFTER the query (see
    #      catalog/material_overview_v3.transform) runs on the already-truncated
    #      result. `LIMIT 20` fetches 20 rows, Python discards 17 of them, the
    #      client gets 3 and concludes there is no more data. Wrong numbers,
    #      status 200.
    #
    # PUSHING FILTERS DOWN IS STILL THE RIGHT DIRECTION -- it is simply
    # all-or-nothing per product. Fetching 100k rows to return 20 wastes the
    # transfer, the driver's deserialisation and the memory, and no index ever
    # gets used. Doing it means, for ONE product, all of:
    #
    #   * every filter of its params model expressed in the query,
    #   * ORDER BY in the query (a window without a stable order is arbitrary),
    #   * SKIP/OFFSET and LIMIT in the query,
    #   * a second COUNT query for meta.total_count -- the dashboard reads that
    #     field to notice truncation (frontend .../data/repository.py), so
    #     dropping it trades a slow table for a quietly incomplete one,
    #   * limit/offset added to the cache key (see cache_key below), which costs
    #     one cache entry and one database round trip PER PAGE instead of one
    #     per filter combination.
    #
    # WHICH PRODUCTS DO IT: catalog/material_search_v1.py is the worked example
    # -- read that one before writing a paged product. It opts in with
    # `paginated_by_source=True` on its DataProduct, and the router then leaves
    # the slicing alone and puts the window into the cache key.
    #
    # material-overview deliberately does NOT, and the reason is its consumer
    # rather than the principle: the dashboard fetches limit=50_000 once and
    # filters client-side, so it never pages. Pushdown would cost it a COUNT
    # query and one cache entry per page for no measurable gain at 64 rows.
    # supplier-risk CANNOT: it joins Neo4j and Postgres and only knows its
    # result after aggregating in Polars, so there is no single query to push a
    # LIMIT into. That is why the flag sits on the product and not in here.
    limit: int = Field(1000, ge=1, le=50_000, description="Maximum number of rows.")
    offset: int = Field(0, ge=0, description="Rows to skip.")

    @field_validator("*", mode="after")
    @classmethod
    def _empty_list_means_no_filter(cls, value: object) -> object:
        """An empty list means "no filter", not "filter on nothing".

        Without this rule an empty multi-select in a dashboard becomes a filter
        that discards everything:

            Python:  status=[]
            Cypher:  WHERE $status IS NULL OR m.status IN $status
                     -> [] IS NULL is false, x IN [] is false
                     -> zero rows, no error, no hint

        The client already strips empty values today, but that is a promise made
        by the caller -- a curl from a notebook, or a Dash callback handing its
        value straight to httpx, would not keep it. So the rule lives here, in
        ONE place, for every data product.

        Requirement: declare list filters as `list[X] | None` so the conversion
        is valid.
        """
        if isinstance(value, list) and not value:
            return None
        return value

    def cache_key(self, *, include_window: bool = False) -> str:
        """The parameters as text -- part of the cache key.

        `limit`/`offset` are deliberately EXCLUDED: the cache holds the loader's
        complete result and the slicing happens afterwards. If they were part of
        the key, every page would be a full re-run (for supplier-risk two
        database queries plus the Polars aggregation) and the same dataset would
        sit in the cache N times. They select a window; they do not define the
        dataset.

        That last sentence stops being true the moment a product pushes
        SKIP/LIMIT into its query (see the note on the fields above). Then the
        window IS part of what was fetched, and leaving it out of the key means
        page 2 gets served page 1's rows -- a data bug, not a performance one.
        So the two decisions are one decision: pushdown and a window-aware cache
        key move together, per product, or neither moves.

        `include_window=True` is that second half. The router passes
        `product.paginated_by_source` here so a caller can never get the pairing
        wrong -- there is no way to switch one on without the other.
        """
        window = set() if include_window else {"limit", "offset"}
        return self.model_dump_json(exclude=window)


class ProductMeta(BaseModel):
    """Appears under "meta" in every response: what, which version, how old?"""

    product: str
    version: str
    api_version: str = "v1"
    generated_at: dt.datetime
    row_count: int
    total_count: int | None = Field(None, description="Rows before limit/offset.")
    source: str = Field("unknown", description="neo4j | postgres | combination.")
    cache: str = Field("miss", description="hit | miss | bypass")
    deprecated: bool = False
    sunset: dt.date | None = None


# --------------------------------------------------------------------------
# The response format.
#
# The next three lines are the only "advanced" part of this file. They make each
# product show its OWN schema in the API documentation under /docs:
#
#     ProductEnvelope[MaterialRowV3]   ->  {"meta": {...}, "data": [MaterialRowV3]}
#     ProductEnvelope[SupplierRiskRow] ->  {"meta": {...}, "data": [SupplierRiskRow]}
#
# `TypeVar` is the placeholder for "some row type", `Generic` tells Pydantic the
# class can be filled in with a type. You only need this in this one place; it
# never comes up when adding a data product.
# --------------------------------------------------------------------------
ItemT = TypeVar("ItemT")


class ProductEnvelope(BaseModel, Generic[ItemT]):
    """Envelope around the data: `meta` + `data`.

    Why an envelope instead of a bare list? Because it tells the dashboard WHICH
    version it received and how old the data is. And because metadata can be
    added later without breaking the contract -- with a bare list, even moving
    to an envelope would itself be a breaking change.
    """

    meta: ProductMeta
    data: list[ItemT]


@dataclass(frozen=True)
class Page:
    """What a `paginated_by_source` loader returns instead of a plain list.

    `total` cannot be derived from `rows` any more: the query already applied
    SKIP/LIMIT, so `len(rows)` is the size of the window, not of the result. The
    loader has to state the total explicitly -- normally from a second COUNT
    query over the SAME filter.

    That "same filter" is the part that rots. If the two queries ever disagree,
    `meta.total_count` starts lying, and the dashboard's truncation warning goes
    with it. See catalog/material_search_v1.py for the way to keep them in sync:
    one shared MATCH/WHERE fragment, two RETURN clauses.
    """

    rows: list[dict[str, Any]]
    total: int


_MAJOR_MINOR = re.compile(r"[0-9]+\.[0-9]+")


@dataclass(frozen=True)
class DataProduct:
    """The description of a data product.

    Created in products/catalog/ and published with `registry.add(...)`.

    On versions: MAJOR.MINOR, e.g. "2.1".
      * field added       -> bump MINOR, same route   (breaks no dashboard)
      * field removed or renamed -> bump MAJOR, new route /v3
      * meaning changed   -> bump MAJOR (even if the schema stays identical!)
    Only the MAJOR appears in the URL path; the full version is in meta.version.
    """

    name: str                       # "material-overview"
    version: str                    # "2.1"
    summary: str                    # one line for the documentation
    item_model: type[BaseModel]     # the row schema = the contract
    loader: Any                     # async def load(sources, params) -> list[dict]
    params_model: type[ProductParams] = ProductParams
    description: str = ""
    tags: tuple[str, ...] = ()
    cache_ttl: int = 60             # seconds; 0 = do not cache
    deprecated: bool = False
    sunset: dt.date | None = None   # when this version will be switched off
    required_groups: tuple[str, ...] = ()
    # Opt-in for query-side pagination. False (the default) means: the loader
    # returns every matching row and the router cuts the window out of it.
    # True means the loader takes limit/offset into its own query and returns a
    # `Page`; the router then does NOT slice again, and limit/offset become part
    # of the cache key. Per product rather than global, because it is not always
    # possible -- supplier-risk only knows its result after joining two sources
    # in Polars, so there is no single query to push a LIMIT into.
    paginated_by_source: bool = False

    def __post_init__(self) -> None:
        if not _MAJOR_MINOR.fullmatch(self.version):
            raise ValueError(
                f"{self.name}: version must be 'MAJOR.MINOR' (e.g. '1.0'), not {self.version!r}."
            )

    @property
    def major(self) -> int:
        """The major version as a number: '2.1' -> 2."""
        return int(self.version.split(".")[0])

    @property
    def path_version(self) -> str:
        """What appears in the URL path: 'v2'."""
        return f"v{self.major}"
