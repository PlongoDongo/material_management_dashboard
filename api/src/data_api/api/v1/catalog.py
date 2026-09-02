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
from pydantic import BaseModel

from data_api.core.security import CurrentPrincipal
from data_api.products.base import DataProduct
from data_api.products.registry import registry

router = APIRouter(prefix="/catalog", tags=["Catalog"])


class VersionInfo(BaseModel):
    version: str
    path: str
    deprecated: bool
    sunset: dt.date | None
    cache_ttl: int
    fields: list[str]


class CatalogEntry(BaseModel):
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
    return [_entry(name, registry.versions_of(name)) for name in registry.names()]


@router.get("/{name}", summary="One data product with all its versions")
async def get_product(name: str, principal: CurrentPrincipal) -> CatalogEntry:
    versions = registry.versions_of(name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown: {name}")
    return _entry(name, versions)
