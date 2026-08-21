"""
Tests der automatischen Architekturdokumentation.

Der wichtigste Test ist der letzte: `test_dokumentation_ist_aktuell`. Er ist der
Grund, warum das Diagramm nicht veralten kann -- wer ein Datenprodukt anlegt und
die Doku nicht neu erzeugt, bekommt einen roten Build statt eines falschen
Diagramms.
"""
from __future__ import annotations

from data_api.architecture import (
    DEFAULT_OUT,
    build,
    collect,
    diagram_contracts,
    diagram_dataflow,
    diagram_versions,
    repositories_used_by,
    repository_sources,
)
from data_api.application import create_app
from data_api.core.config import Settings
from data_api.products.catalog.material_overview_v2 import load as load_material
from data_api.products.catalog.supplier_risk_v1 import load as load_risk


def test_ast_erkennt_die_genutzten_repositories():
    """Die Kernidee: welche Quelle ein Produkt nutzt, wird gelesen, nicht gepflegt."""
    assert repositories_used_by(load_material) == ["materials"]
    assert repositories_used_by(load_risk) == ["deliveries", "materials"]


def test_ast_erkennt_die_adapter_je_repository():
    quellen = repository_sources()
    assert quellen["materials"] == ["neo4j"]
    assert quellen["deliveries"] == ["postgres"]


def test_collect_findet_generierte_routen(settings: Settings):
    """Genau diese Routen existieren NICHT im Quelltext -- nur zur Laufzeit."""
    arch = collect(create_app(settings))
    pfade = {r.path for r in arch.routes}
    assert "/api/v1/data-products/material-overview/v2" in pfade
    assert "/api/v1/data-products/supplier-risk/v1" in pfade
    assert len(arch.products) == 3


def test_routen_werden_ihrem_datenprodukt_zugeordnet(settings: Settings):
    arch = collect(create_app(settings))
    nach_pfad = {r.path: r for r in arch.routes}

    v1 = nach_pfad["/api/v1/data-products/material-overview/v1"]
    assert v1.product.version == "1.2"
    assert v1.deprecated is True

    alias = nach_pfad["/api/v1/data-products/material-overview/latest"]
    assert alias.is_alias is True
    assert alias.product.version == "2.0"

    # Handgeschriebene Routen haben kein Datenprodukt
    assert nach_pfad["/api/v1/healthz"].product is None


def test_produkte_kennen_ihre_quellen(settings: Settings):
    arch = collect(create_app(settings))
    risiko = next(p for p in arch.products if p.product.name == "supplier-risk")
    assert risiko.repositories == ["deliveries", "materials"]
    assert set(risiko.sources) == {"neo4j", "postgres"}


def test_diagramme_enthalten_die_erwarteten_knoten(settings: Settings):
    arch = collect(create_app(settings))

    dataflow = diagram_dataflow(arch)
    assert dataflow.startswith("flowchart")
    assert "supplier-risk" in dataflow
    assert "src_neo4j" in dataflow and "src_postgres" in dataflow
    # Der Alias verdoppelt nur Kanten und gehoert nicht ins Flussdiagramm
    assert "latest" not in dataflow

    assert "abgeloest durch" in diagram_versions(arch)
    assert "bestandswert" in diagram_contracts(arch)


def test_markdown_enthaelt_alle_abschnitte():
    markdown = build()
    assert markdown.count("```mermaid") == 3
    assert "## Datenfluss" in markdown
    assert "## Routeninventar" in markdown
    assert "team-supply-chain" in markdown


def test_dokumentation_ist_aktuell():
    """Schlaegt fehl, wenn jemand die Architektur aendert und die Doku nicht neu erzeugt.

    Reparatur: `architecture-docs` ausfuehren und das Ergebnis mit einchecken.
    """
    assert DEFAULT_OUT.exists(), "docs/architecture.md fehlt -- 'architecture-docs' ausfuehren."
    assert DEFAULT_OUT.read_text(encoding="utf-8") == build(), (
        "docs/architecture.md ist veraltet -- 'architecture-docs' ausfuehren."
    )
