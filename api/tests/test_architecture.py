"""
Tests of the generated architecture documentation.

The last test is the important one: `test_documentation_is_current`. It is why
the diagram cannot go stale -- anyone who adds a data product without
regenerating gets a red build instead of a quietly wrong diagram.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi.routing import APIRoute

from data_api.api.v1 import API_V1_PREFIX, TOPIC_ROUTERS
from data_api.app import create_app
from data_api.architecture import (
    DEFAULT_OUT,
    build,
    collect,
    diagram_contracts,
    diagram_dataflow,
    diagram_versions,
    render_markdown,
)
from data_api.core.config import Settings
from data_api.products.cache import cache, invalidates
from data_api.products.catalog.material_overview_v3 import load as load_material
from data_api.products.catalog.supplier_risk_v2 import load as load_risk
from data_api.products.introspect import sources_used_by
from data_api.products.registry import registry


def test_the_ast_finds_the_sources_a_product_uses() -> None:
    """The core idea: which source a product uses is read, not maintained."""
    assert sources_used_by(load_material) == ["neo4j"]
    assert sources_used_by(load_risk) == ["neo4j", "postgres"]


def test_collect_finds_the_generated_routes(settings: Settings) -> None:
    """These routes do NOT exist in the source code -- only at runtime."""
    arch = collect(create_app(settings))
    paths = {r.path for r in arch.routes}
    assert "/api/v1/data-products/material-overview/v3" in paths
    assert "/api/v1/data-products/supplier-risk/v2" in paths

    # Deliberately not a count: every new product in catalog/ would fail it, and
    # a test that goes red for the right change teaches people to edit the
    # number without reading it. What matters is that discovery found the
    # registry and produced a route per product.
    assert len(arch.products) == len(registry.all())
    assert arch.products


def test_routes_are_mapped_to_their_data_product(settings: Settings) -> None:
    arch = collect(create_app(settings))
    by_path = {r.path: r for r in arch.routes}

    v2 = by_path["/api/v1/data-products/material-overview/v2"]
    assert v2.product.version == "2.1"
    assert v2.deprecated is True

    alias = by_path["/api/v1/data-products/material-overview/latest"]
    assert alias.is_alias is True
    assert alias.product.version == "3.0"

    # hand-written routes have no data product
    assert by_path["/api/v1/healthz"].product is None


def test_products_know_their_sources(settings: Settings) -> None:
    arch = collect(create_app(settings))
    risk = next(p for p in arch.products if p.product.name == "supplier-risk")
    assert risk.sources == ["neo4j", "postgres"]
    material = next(p for p in arch.products if p.product.major == 3)
    assert material.sources == ["neo4j"]      # no Postgres -> no edge in the diagram


def test_the_diagrams_contain_the_expected_nodes(settings: Settings) -> None:
    arch = collect(create_app(settings))

    dataflow = diagram_dataflow(arch)
    assert dataflow.startswith("flowchart")
    assert "supplier-risk" in dataflow
    assert "src_neo4j" in dataflow and "src_postgres" in dataflow
    # the alias only duplicates edges and does not belong in the flow diagram
    assert "latest" not in dataflow

    assert "superseded by" in diagram_versions(arch)
    assert "stock_value" in diagram_contracts(arch)


def test_the_markdown_contains_every_section() -> None:
    markdown = build()
    assert markdown.count("```mermaid") == 3
    assert "## Data flow" in markdown
    assert "## Route inventory" in markdown
    assert "team-supply-chain" in markdown


def test_documentation_is_current() -> None:
    """Fails if somebody changes the architecture without regenerating.

    Fix: run `architecture-docs` and commit the result.
    """
    assert DEFAULT_OUT.exists(), "docs/architecture.md is missing -- run 'architecture-docs'."
    assert DEFAULT_OUT.read_text(encoding="utf-8") == build(), (
        "docs/architecture.md is out of date -- run 'architecture-docs'."
    )


# --- Every write route carries its guards -----------------------------------
#
# Reads are generated, so their checks cannot be forgotten -- products/router.py
# applies them to every product. Writes are hand-written, and that is exactly
# where a forgotten line is silent: a route without a role check is open, and a
# route without cache invalidation makes the dashboard show the old state for up
# to cache_ttl seconds while the user believes the save failed.
#
# So instead of generating write routes (which would buy little -- the handler
# body is the work), the two cross-cutting concerns are declared as dependencies
# and this test insists they are there.

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _write_routes() -> list[tuple[str, APIRoute]]:
    """(full path, route) for every hand-written route that changes something."""
    found = []
    for router in TOPIC_ROUTERS:
        for route in router.routes:
            if isinstance(route, APIRoute) and WRITE_METHODS & route.methods:
                found.append((f"{API_V1_PREFIX}{route.path}", route))
    return found


def _declared(route: APIRoute, attribute: str) -> tuple[str, ...]:
    """What a route's dependencies declare under `attribute`."""
    values: tuple[str, ...] = ()
    for dependency in route.dependencies:
        values += tuple(getattr(dependency.dependency, attribute, ()))
    return values


def test_there_are_write_routes_to_check() -> None:
    """Guards the guard: if the discovery above silently found nothing, every
    test below would pass for the wrong reason."""
    assert _write_routes(), "no write routes discovered -- is TOPIC_ROUTERS still right?"


def test_every_write_route_demands_a_role() -> None:
    """A hand-written route without `requires(...)` is open, and nothing says so."""
    for path, route in _write_routes():
        assert _declared(route, "required_groups"), (
            f"{sorted(route.methods)} {path} has no requires(...) dependency"
        )


def test_every_write_route_invalidates_something() -> None:
    """Writing without evicting the read cache shows the user a stale table and
    makes them believe the save failed.

    If a write genuinely affects no data product, say so explicitly with
    `invalidates()` -- an empty call is a decision, a missing one is an
    oversight, and this test cannot tell them apart otherwise.
    """
    for path, route in _write_routes():
        assert any(
            hasattr(dependency.dependency, "invalidated_products")
            for dependency in route.dependencies
        ), f"{sorted(route.methods)} {path} has no invalidates(...) dependency"


def test_invalidated_products_actually_exist() -> None:
    """A typo in the product name evicts nothing, and nothing complains -- the
    cache key simply never matches."""
    known = set(registry.names())
    for path, route in _write_routes():
        for product in _declared(route, "invalidated_products"):
            assert product in known, (
                f"{path} invalidates '{product}', which is not a registered data "
                f"product. Known: {sorted(known)}"
            )


# --- Write routes reach the generated documentation --------------------------

def test_write_routes_appear_in_the_diagram_with_their_invalidations(
    settings: Settings,
) -> None:
    """The edge a developer cannot see from either side's code alone.

    "POST /mappings makes material-overview stale" is written down in neither
    file: the route does not know who caches it, and the product does not know
    who changes it. Only the diagram puts the two together -- which is the whole
    reason to generate it.
    """
    arch = collect(create_app(settings))
    diagram = diagram_dataflow(arch)

    writes = [route for route in arch.routes if route.is_write]
    assert writes, "no write routes collected"

    # NOT "every write route invalidates something": a write that genuinely
    # affects no data product declares `invalidates()` with no arguments, and
    # that is a legitimate answer (see test_every_write_route_invalidates_
    # something). Requiring a non-empty list here would fail the build for
    # exactly the case the design allows -- what has to hold is that whatever a
    # route DOES declare shows up as an edge.
    drawn = [route for route in writes if route.invalidates]
    assert drawn, "no write route declares an invalidation -- nothing to draw"
    for route in drawn:
        for product in route.invalidates:
            assert product.replace("-", "_") in diagram
        assert "invalidates" in diagram


def test_the_write_route_table_lists_role_and_invalidation(settings: Settings) -> None:
    """Read back off the dependencies, so the table cannot claim something the
    routes do not actually enforce."""
    markdown = render_markdown(collect(create_app(settings)))

    assert "## Write routes" in markdown
    assert "| `/api/v1/mappings` | POST | material-planner |" in markdown
    assert "material-overview |" in markdown


def test_a_read_route_is_not_marked_as_a_write(settings: Settings) -> None:
    """Guards the method check: if everything counted as a write, the tests
    above would pass while saying nothing."""
    arch = collect(create_app(settings))
    reads = [route for route in arch.routes if not route.is_write]

    assert reads
    assert all(not route.invalidates for route in reads)


def test_a_write_that_affects_nothing_may_say_so(settings: Settings) -> None:
    """`invalidates()` with no arguments is a supported answer.

    A health probe, a job trigger, a write to a table nothing reads yet -- those
    exist, and they still have to declare the dependency. The guard asks whether
    it is THERE, not whether the list is non-empty, so an empty call passes
    while a missing one does not. Without this test the two guards above could
    drift into demanding a non-empty list and would then fail the build for
    exactly the case the design allows.
    """
    empty = invalidates()

    assert empty.invalidated_products == ()
    assert hasattr(empty, "invalidated_products")

    # And it does nothing at runtime: the cache survives the dependency.
    key = cache.make_key("material-overview", 3, "{}")
    cache.set(key, (["row"], 1, "neo4j", None), ttl=60)
    asyncio.run(_drain(empty()))
    assert cache.get(key) is not None


async def _drain(generator: AsyncIterator[None]) -> None:
    """Runs a yield-dependency past its yield, the way FastAPI does."""
    await generator.__anext__()
    with contextlib.suppress(StopAsyncIteration):
        await generator.__anext__()
