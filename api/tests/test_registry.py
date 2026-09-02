"""Tests of the registry -- including its safeguards."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from data_api.products.base import DataProduct
from data_api.products.registry import ProductRegistry


class Row(BaseModel):
    x: int


async def _loader(sources, params):
    return []


def _product(name: str, version: str) -> DataProduct:
    return DataProduct(name=name, version=version, summary="s",
                       item_model=Row, loader=_loader)


def test_path_contains_only_the_major():
    assert _product("p", "2.7").path_version == "v2"
    assert _product("p", "2.7").version == "2.7"


def test_an_invalid_version_fails_immediately():
    with pytest.raises(ValueError, match="MAJOR.MINOR"):
        _product("p", "v1")


def test_a_collision_on_the_same_major_is_rejected():
    """Two products on the same route would be a silent data error."""
    registry = ProductRegistry()
    registry.add(_product("p", "1.0"))
    with pytest.raises(ValueError, match="collision"):
        registry.add(_product("p", "1.5"))


def test_different_majors_coexist():
    registry = ProductRegistry()
    registry.add(_product("p", "1.0"))
    registry.add(_product("p", "2.0"))
    assert len(registry.versions_of("p")) == 2
    assert registry.latest("p").version == "2.0"


def test_latest_skips_deprecated_versions():
    registry = ProductRegistry()
    registry.add(_product("p", "1.0"))
    retired = DataProduct(name="p", version="2.0", summary="s", item_model=Row,
                          loader=_loader, deprecated=True)
    registry.add(retired)
    assert registry.latest("p").version == "1.0"
