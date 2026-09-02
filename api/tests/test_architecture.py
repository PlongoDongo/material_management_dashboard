"""
Tests of the generated architecture documentation.

The last test is the important one: `test_documentation_is_current`. It is why
the diagram cannot go stale -- anyone who adds a data product without
regenerating gets a red build instead of a quietly wrong diagram.
"""
from __future__ import annotations

from data_api.architecture import (
    DEFAULT_OUT,
    build,
    collect,
    diagram_contracts,
    diagram_dataflow,
    diagram_versions,
)
from data_api.application import create_app
from data_api.core.config import Settings
from data_api.products.catalog.material_overview_v3 import load as load_material
from data_api.products.catalog.supplier_risk_v2 import load as load_risk
from data_api.products.introspect import sources_used_by


def test_the_ast_finds_the_sources_a_product_uses():
    """The core idea: which source a product uses is read, not maintained."""
    assert sources_used_by(load_material) == ["neo4j"]
    assert sources_used_by(load_risk) == ["neo4j", "postgres"]


def test_collect_finds_the_generated_routes(settings: Settings):
    """These routes do NOT exist in the source code -- only at runtime."""
    arch = collect(create_app(settings))
    paths = {r.path for r in arch.routes}
    assert "/api/v1/data-products/material-overview/v3" in paths
    assert "/api/v1/data-products/supplier-risk/v2" in paths
    assert len(arch.products) == 3


def test_routes_are_mapped_to_their_data_product(settings: Settings):
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


def test_products_know_their_sources(settings: Settings):
    arch = collect(create_app(settings))
    risk = next(p for p in arch.products if p.product.name == "supplier-risk")
    assert risk.sources == ["neo4j", "postgres"]
    material = next(p for p in arch.products if p.product.major == 3)
    assert material.sources == ["neo4j"]      # no Postgres -> no edge in the diagram


def test_the_diagrams_contain_the_expected_nodes(settings: Settings):
    arch = collect(create_app(settings))

    dataflow = diagram_dataflow(arch)
    assert dataflow.startswith("flowchart")
    assert "supplier-risk" in dataflow
    assert "src_neo4j" in dataflow and "src_postgres" in dataflow
    # the alias only duplicates edges and does not belong in the flow diagram
    assert "latest" not in dataflow

    assert "superseded by" in diagram_versions(arch)
    assert "stock_value" in diagram_contracts(arch)


def test_the_markdown_contains_every_section():
    markdown = build()
    assert markdown.count("```mermaid") == 3
    assert "## Data flow" in markdown
    assert "## Route inventory" in markdown
    assert "team-supply-chain" in markdown


def test_documentation_is_current():
    """Fails if somebody changes the architecture without regenerating.

    Fix: run `architecture-docs` and commit the result.
    """
    assert DEFAULT_OUT.exists(), "docs/architecture.md is missing -- run 'architecture-docs'."
    assert DEFAULT_OUT.read_text(encoding="utf-8") == build(), (
        "docs/architecture.md is out of date -- run 'architecture-docs'."
    )
