"""
The catalog: which data products exist, in which versions, and who owns them?

This is not decoration. Once there are more than a handful of products, the
catalog is the answer to "has somebody already built this?" -- and it can be
consumed programmatically (to fill a product picker in a dashboard, or to check
in CI that no product was committed without an owner).

It is generated from the same registry as the routes, so it cannot go stale.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.security import CurrentPrincipal
from products.base import DataProduct
from products.registry import registry

router = APIRouter(prefix="/catalog", tags=["Catalog"])


class VersionInfo(BaseModel):
    """One published version of a data product: what to call, what it returns.

    `path` is the route to request. `fields` are the column names this version
    returns -- the quickest way to see what changed between two versions.
    `sunset` is only set for a deprecated version and names its planned removal
    date. `cache_ttl` is how many seconds the API may answer from its cache.
    """

    version: str
    path: str
    deprecated: bool
    sunset: dt.date | None
    cache_ttl: int
    fields: list[str] = Field(description="Column names this version returns.")


class CatalogEntry(BaseModel):
    """A data product with every version the caller is allowed to fetch.

    `owner` is the team to ask about the data. `latest` is the highest version,
    for orientation only -- dashboards should pin a fixed `path` from `versions`,
    so a new major version cannot break them unannounced.
    """

    name: str
    summary: str
    owner: str
    tags: list[str]
    latest: str
    versions: list[VersionInfo]


def _entry(name: str, versions: list[DataProduct]) -> CatalogEntry:
    # `versions` comes from the caller, who has already checked it is not empty.
    # This used to be `assert newest is not None` -- which disappears under
    # `python -O` and would then be an AttributeError on None.
    newest = max(versions, key=lambda p: p.major)
    return CatalogEntry(
        name=name,
        summary=newest.summary,
        owner=newest.owner,
        tags=list(newest.tags),
        latest=newest.version,
        versions=[
            VersionInfo(
                version=p.version,
                path=f"/api/v1/data-products/{p.name}/{p.path_version}",
                deprecated=p.deprecated,
                sunset=p.sunset,
                cache_ttl=p.cache_ttl,
                fields=list(p.item_model.model_fields),
            )
            for p in versions
        ],
    )


# The catalog requires THE SAME authentication as the data products. It lists
# names, owners, cache times, sunset dates and every contract field -- the full
# map of what sits behind the auth. Leaving it open would be a decision; it just
# had not been made.
@router.get("", summary="All available data products")
async def list_products(principal: CurrentPrincipal) -> list[CatalogEntry]:
    """Only the products this caller may actually fetch.

    The catalog is what the dashboard reads to decide which pages to offer, so
    listing a product the caller would get a 403 on is worse than useless -- it
    produces a menu entry that breaks when clicked. Filtering here also stops
    the catalog from leaking the existence of restricted products.
    """
    entries = []
    for name in registry.names():
        allowed = [product for product in registry.versions_of(name)
                   if principal.may_access(product.required_groups)]
        if allowed:
            entries.append(_entry(name, allowed))
    return entries


@router.get("/{name}", summary="One data product with all its versions")
async def get_product(name: str, principal: CurrentPrincipal) -> CatalogEntry:
    versions = [p for p in registry.versions_of(name)
                if principal.may_access(p.required_groups)]
    if not versions:
        # 404 rather than 403, and deliberately the same answer as for a name
        # that does not exist: otherwise the error code itself would tell an
        # unauthorised caller which products exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown: {name}")
    return _entry(name, versions)
