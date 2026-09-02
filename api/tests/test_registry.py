"""Tests der Registry -- inklusive der Schutzmechanismen."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from data_api.products.base import DataProduct
from data_api.products.registry import ProductRegistry


class Row(BaseModel):
    x: int


async def _loader(sources, params):
    return []


def _produkt(name: str, version: str) -> DataProduct:
    return DataProduct(name=name, version=version, summary="s",
                       item_model=Row, loader=_loader)


def test_pfad_enthaelt_nur_das_major():
    assert _produkt("p", "2.7").path_version == "v2"
    assert _produkt("p", "2.7").version == "2.7"


def test_ungueltige_version_faellt_sofort_auf():
    with pytest.raises(ValueError, match="MAJOR.MINOR"):
        _produkt("p", "v1")


def test_kollision_gleicher_major_wird_verhindert():
    """Zwei Produkte auf derselben Route waeren ein stiller Datenfehler."""
    registry = ProductRegistry()
    registry.add(_produkt("p", "1.0"))
    with pytest.raises(ValueError, match="Kollision"):
        registry.add(_produkt("p", "1.5"))


def test_verschiedene_majors_koexistieren():
    registry = ProductRegistry()
    registry.add(_produkt("p", "1.0"))
    registry.add(_produkt("p", "2.0"))
    assert len(registry.versions_of("p")) == 2
    assert registry.latest("p").version == "2.0"


def test_latest_ueberspringt_deprecated_versionen():
    registry = ProductRegistry()
    registry.add(_produkt("p", "1.0"))
    veraltet = DataProduct(name="p", version="2.0", summary="s", item_model=Row,
                           loader=_loader, deprecated=True)
    registry.add(veraltet)
    assert registry.latest("p").version == "1.0"
