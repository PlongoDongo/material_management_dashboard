"""
What is a data product?

A data product is a named, versioned dataset with an owner -- not simply "a
route that happens to query the database".

This file contains four things:

    ProductParams     base class for a product's allowed query parameters
    ProductMeta       the metadata every response carries
    ProductEnvelope   the response format: {"meta": {...}, "data": [...]}
    DataProduct       the description of a product (name, version, loader, ...)
"""
from __future__ import annotations

import datetime as dt
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
    # router -- NOT in the query. If SKIP/LIMIT also appear in the Cypher, the
    # result is sliced twice and every page after the first comes back empty.
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

    def cache_key(self) -> str:
        """The parameters as text -- part of the cache key.

        `limit`/`offset` are deliberately EXCLUDED: the cache holds the loader's
        complete result and the slicing happens afterwards. If they were part of
        the key, every page would be a full re-run (for supplier-risk two
        database queries plus the Polars aggregation) and the same dataset would
        sit in the cache N times. They select a window; they do not define the
        dataset.
        """
        return self.model_dump_json(exclude={"limit", "offset"})


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
    owner: str = "unassigned"       # who to ask about this product
    description: str = ""
    tags: tuple[str, ...] = ()
    cache_ttl: int = 60             # seconds; 0 = do not cache
    deprecated: bool = False
    sunset: dt.date | None = None   # when this version will be switched off
    required_groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        parts = self.version.split(".")
        if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
            raise ValueError(
                f"{self.name}: version must be 'MAJOR.MINOR' (e.g. '1.0'), "
                f"not {self.version!r}."
            )

    @property
    def major(self) -> int:
        """The major version as a number: '2.1' -> 2."""
        return int(self.version.split(".")[0])

    @property
    def path_version(self) -> str:
        """What appears in the URL path: 'v2'."""
        return f"v{self.major}"
