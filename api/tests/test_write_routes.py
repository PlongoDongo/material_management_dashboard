"""
Every hand-written write route carries its two guards.

Reads are generated, so their checks cannot be forgotten -- products/router.py
applies them to every product. Writes are hand-written, and a forgotten line is
silent there: a route without a role check is open, and a route without cache
invalidation shows the dashboard the old state for up to cache_ttl seconds while
the user believes the save failed.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from tests.fakes import FakeSources

from api.deps import get_sources
from api.v1 import API_V1_PREFIX, TOPIC_ROUTERS
from app import create_app
from core.config import Settings
from products.cache import cache, invalidates
from products.registry import discover, registry

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _write_routes() -> list[tuple[str, APIRoute]]:
    """(full path, route) for every hand-written route that changes something."""
    return [
        (f"{API_V1_PREFIX}{route.path}", route)
        for router in TOPIC_ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute) and WRITE_METHODS & route.methods
    ]


def _declared(route: APIRoute, attribute: str) -> tuple[str, ...]:
    """What a route's dependencies declare under `attribute`."""
    values: tuple[str, ...] = ()
    for dependency in route.dependencies:
        values += tuple(getattr(dependency.dependency, attribute, ()))
    return values


def test_write_routes_are_found_and_reads_are_not() -> None:
    """Guards the guard: finding nothing, or counting every route as a write,
    would let every test below pass for the wrong reason."""
    paths = {path for path, _ in _write_routes()}

    assert f"{API_V1_PREFIX}/material-relationships" in paths, "is TOPIC_ROUTERS still right?"
    assert f"{API_V1_PREFIX}/healthz" not in paths


def test_every_write_route_demands_a_role() -> None:
    """A hand-written route without `requires(...)` is open, and nothing says so."""
    for path, route in _write_routes():
        assert _declared(route, "required_groups"), (
            f"{sorted(route.methods)} {path} has no requires(...) dependency"
        )


def test_every_write_route_invalidates_something() -> None:
    """If a write genuinely affects no data product, say so with `invalidates()`
    -- an empty call is a decision, a missing one is an oversight."""
    for path, route in _write_routes():
        assert any(
            hasattr(dependency.dependency, "invalidated_products")
            for dependency in route.dependencies
        ), f"{sorted(route.methods)} {path} has no invalidates(...) dependency"


def test_invalidated_products_actually_exist() -> None:
    """A typo in the product name evicts nothing, and nothing complains -- the
    cache key simply never matches."""
    discover()
    known = set(registry.names())
    for path, route in _write_routes():
        for product in _declared(route, "invalidated_products"):
            assert product in known, (
                f"{path} invalidates '{product}', which is not a registered data "
                f"product. Known: {sorted(known)}"
            )


def test_a_write_that_affects_nothing_may_say_so() -> None:
    """`invalidates()` with no arguments passes the guard and evicts nothing."""
    empty = invalidates()
    assert empty.invalidated_products == ()

    key = cache.make_key("material-overview", 3, "{}")
    cache.set(key, (["row"], 1, "neo4j", None), ttl=60)
    asyncio.run(_drain(empty()))
    assert cache.get(key) is not None


async def _drain(generator: AsyncIterator[None]) -> None:
    """Runs a yield-dependency past its yield, the way FastAPI does."""
    await generator.__anext__()
    with contextlib.suppress(StopAsyncIteration):
        await generator.__anext__()


def test_a_declared_invalidation_really_evicts_the_product(settings: Settings) -> None:
    """The mechanism end to end: after a write the next read goes to the source
    again instead of being answered from the cache.

    On a route declared here rather than on a real one: no production route
    invalidates a product today -- the relationship routes feed an outbox that a
    separate process applies later -- and a route claiming otherwise would put a
    false statement into /docs.
    """
    application: FastAPI = create_app(settings)
    application.dependency_overrides[get_sources] = FakeSources

    @application.post("/test-write", dependencies=[Depends(invalidates("material-overview"))])
    async def _write() -> dict[str, bool]:
        return {"written": True}

    path = f"{API_V1_PREFIX}/data-products/material-overview/v3"
    cache.invalidate()
    with TestClient(application) as client:
        client.get(path)
        assert client.get(path).json()["meta"]["cache"] == "hit"

        assert client.post("/test-write").status_code == 200

        assert client.get(path).json()["meta"]["cache"] == "miss"
