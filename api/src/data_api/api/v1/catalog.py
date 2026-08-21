"""
Der Katalog: welche Datenprodukte gibt es, in welchen Versionen, wem gehoeren sie?

Das ist kein Beiwerk. Sobald mehr als eine Handvoll Produkte existiert, ist der
Katalog die Antwort auf "hat das schon jemand gebaut?" -- und er laesst sich
maschinell auswerten (z. B. um in einem Dashboard eine Produktauswahl zu
fuellen, oder um in CI zu pruefen, ob ein Produkt ohne Owner eingecheckt wurde).

Er wird aus derselben Registry erzeugt wie die Routen; er kann also nicht
veralten.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from data_api.products.registry import registry

router = APIRouter(prefix="/catalog", tags=["Katalog"])


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


def _entry(name: str) -> CatalogEntry:
    versions = registry.versions_of(name)
    newest = registry.latest(name)
    assert newest is not None
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


@router.get("", summary="Alle verfuegbaren Datenprodukte")
async def list_products() -> list[CatalogEntry]:
    return [_entry(name) for name in registry.names()]


@router.get("/{name}", summary="Ein Datenprodukt mit allen Versionen")
async def get_product(name: str) -> CatalogEntry:
    if not registry.versions_of(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unbekannt: {name}")
    return _entry(name)
