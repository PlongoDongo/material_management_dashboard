"""
Erzeugt die visuelle Architekturdokumentation aus der LAUFENDEN App.

    python -m data_api.architecture --out ../docs/architecture.md
    python -m data_api.architecture --check          # CI: schlaegt fehl, wenn veraltet

Warum selbst gebaut und kein fertiges Paket?

Generische Werkzeuge (fastapi-router-viz, fastapi-di-viz, pydeps) analysieren
entweder den Quelltext oder die Dependency-Kette. Beides greift hier zu kurz:

  * Unsere Datenprodukt-Routen EXISTIEREN NICHT im Quelltext -- sie entstehen
    zur Laufzeit aus der Registry. Ein statischer Parser sieht sie schlicht nicht.
  * Alle Routen haengen an derselben Dependency (`get_sources`). Ein DI-Graph
    zeigt darum fuer jede Route dasselbe Bild und verraet nichts darueber,
    WELCHE Quelle ein Produkt tatsaechlich nutzt.

Diese 200 Zeilen wissen dagegen, was die Werkzeuge raten muessten: Version,
Owner, Cache-TTL, Deprecation, Vertragsfelder -- alles steht schon in der
Registry. Die fehlende Information (welches Produkt nutzt welches Repository)
wird per AST aus dem Loader gelesen (products/introspect.py), statt sie von
Hand zu pflegen.

Konsequenz: Das Diagramm kann nicht veralten. Wer ein Datenprodukt anlegt oder
eine Quelle wechselt, aendert das Diagramm automatisch mit -- und `--check`
sorgt dafuer, dass niemand vergisst, es neu zu erzeugen.
"""
from __future__ import annotations

import argparse
import ast
import inspect
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from data_api.products.base import DataProduct
from data_api.products.introspect import sources_used_by


# ---------------------------------------------------------------------------
# Einsammeln (Introspektion)
# ---------------------------------------------------------------------------

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass
class RouteInfo:
    path: str
    methods: list[str]
    summary: str
    tags: list[str]
    deprecated: bool
    product: DataProduct | None = None
    is_alias: bool = False


@dataclass
class ProductInfo:
    product: DataProduct
    sources: list[str] = field(default_factory=list)


@dataclass
class Architecture:
    routes: list[RouteInfo]
    products: list[ProductInfo]


def _first_arg_name(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    return fn.args.args[0].arg if fn.args.args else None


def _parse_function(obj: Any) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    """Holt den AST einer Funktion. Dekoratoren stoeren dabei nicht."""
    try:
        source = textwrap.dedent(inspect.getsource(obj))
    except (OSError, TypeError):
        return None
    node = ast.parse(source).body[0]
    return node if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) else None


def collect(app: FastAPI) -> Architecture:
    from data_api.products.registry import registry

    products = [
        ProductInfo(product=product, sources=sources_used_by(product.loader))
        for product in registry.all()
    ]

    # Routen aus dem OpenAPI-Schema lesen, nicht aus `app.routes`.
    # Der Grund: FastAPI haelt eingebundene Router intern als `_IncludedRouter`
    # -- eine private Struktur, die sich zwischen Versionen aendert (genau das
    # ist beim Bauen hier passiert). Das OpenAPI-Schema ist dagegen der
    # oeffentliche, stabile Vertrag der App und enthaelt alles, was wir
    # brauchen: Pfad, Methoden, Summary, Tags, Deprecated-Flag.
    routes: list[RouteInfo] = []
    schema = app.openapi()
    for path, operations in schema.get("paths", {}).items():
        methods = sorted(m.upper() for m in operations if m.lower() in _HTTP_METHODS)
        if not methods:
            continue
        operation = operations[methods[0].lower()]

        product, is_alias = None, False
        parts = path.strip("/").split("/")
        if "data-products" in parts:
            index = parts.index("data-products")
            name, version = parts[index + 1], parts[index + 2]
            is_alias = version == "latest"
            newest = registry.latest(name)
            major = newest.major if is_alias else int(version.lstrip("v"))
            product = registry.get(name, major)

        routes.append(
            RouteInfo(
                path=path,
                methods=methods,
                summary=operation.get("summary", ""),
                tags=[str(t) for t in operation.get("tags", [])],
                deprecated=bool(operation.get("deprecated", False)),
                product=product,
                is_alias=is_alias,
            )
        )
    routes.sort(key=lambda r: r.path)
    return Architecture(routes=routes, products=products)


# ---------------------------------------------------------------------------
# Rendern (Mermaid)
# ---------------------------------------------------------------------------

def _id(*parts: str) -> str:
    """Mermaid-Knoten-IDs duerfen keine Sonderzeichen enthalten."""
    raw = "_".join(parts)
    return "".join(c if c.isalnum() or c == "_" else "_" for c in raw)


def diagram_dataflow(arch: Architecture) -> str:
    """Das Hauptdiagramm: Route -> Datenprodukt -> Datenquelle."""
    lines = [
        "flowchart LR",
        "  subgraph clients[\"Konsumenten\"]",
        "    dash[\"Dash-Dashboards\"]",
        "  end",
        "",
        "  subgraph routes[\"Routen /api/v1\"]",
    ]
    for route in arch.routes:
        if route.is_alias:
            continue          # Alias zeigt auf dieselbe Version -- verdoppelt nur Kanten
        label = route.path.replace("/api/v1", "")
        marker = " ⚠" if route.deprecated else ""
        lines.append(f'    {_id("r", route.path)}["{"/".join(m for m in route.methods)} '
                     f'{label}{marker}"]')
    lines.append("  end")
    lines.append("")

    lines.append('  subgraph products["Datenprodukte"]')
    for info in arch.products:
        node = _id("p", info.product.name, str(info.product.major))
        marker = " ⚠" if info.product.deprecated else ""
        lines.append(f'    {node}["{info.product.name}<br/>v{info.product.major} '
                     f'· {info.product.version}{marker}"]')
    lines.append("  end")
    lines.append("")

    all_sources = sorted({s for info in arch.products for s in info.sources})
    lines.append('  subgraph sources["Datenquellen"]')
    for source in all_sources:
        shape = f'[("{source}")]'
        lines.append(f'    {_id("src", source)}{shape}')
    lines.append("  end")
    lines.append("")

    lines.append("  dash --> routes")
    for route in arch.routes:
        if route.is_alias or route.product is None:
            continue
        lines.append(f'  {_id("r", route.path)} --> '
                     f'{_id("p", route.product.name, str(route.product.major))}')
    for info in arch.products:
        product_node = _id("p", info.product.name, str(info.product.major))
        for source in info.sources:
            lines.append(f"  {product_node} --> {_id('src', source)}")

    lines += [
        "",
        "  classDef deprecated stroke-dasharray: 4 3;",
    ]
    veraltet = [_id("p", i.product.name, str(i.product.major))
                for i in arch.products if i.product.deprecated]
    if veraltet:
        lines.append(f"  class {','.join(veraltet)} deprecated;")
    return "\n".join(lines)


def diagram_versions(arch: Architecture) -> str:
    """Versionsstaende je Produktfamilie -- was ist live, was laeuft aus."""
    families: dict[str, list[ProductInfo]] = {}
    for info in arch.products:
        families.setdefault(info.product.name, []).append(info)

    lines = ["flowchart LR"]
    for name, infos in sorted(families.items()):
        lines.append(f'  subgraph {_id("f", name)}["{name}"]')
        lines.append("    direction LR")
        previous = None
        for info in sorted(infos, key=lambda i: i.product.major):
            node = _id("v", name, str(info.product.major))
            product = info.product
            status = "auslaufend" if product.deprecated else "aktiv"
            sunset = f"<br/>Sunset {product.sunset}" if product.sunset else ""
            lines.append(f'    {node}["v{product.major} · {product.version}'
                         f'<br/>{status}{sunset}"]')
            if previous:
                lines.append(f"    {previous} -.->|abgeloest durch| {node}")
            previous = node
        lines.append("  end")
    return "\n".join(lines)


def diagram_contracts(arch: Architecture) -> str:
    """Die Vertraege selbst -- welche Felder liefert welche Version."""
    lines = ["classDiagram"]
    for info in arch.products:
        model = info.product.item_model
        cls = model.__name__
        lines.append(f"  class {cls} {{")
        for field_name, field_info in model.model_fields.items():
            annotation = field_info.annotation
            type_name = getattr(annotation, "__name__", str(annotation))
            type_name = (type_name.replace("typing.", "").replace("Optional", "")
                         .replace(" | None", "?").replace("None", "").strip(" []|"))
            lines.append(f"    +{type_name or 'any'} {field_name}")
        lines.append("  }")
        lines.append(f'  note for {cls} "{info.product.name} v{info.product.major}"')
    return "\n".join(lines)


def render_markdown(arch: Architecture) -> str:
    # BEWUSST kein Zeitstempel: Der Inhalt dieser Datei muss eine reine Funktion
    # des Codes sein. Stuende hier das Tagesdatum, schluege der Veraltungs-Check
    # (tests/test_architecture.py) jeden Tag fehl, ohne dass sich etwas geaendert
    # haette -- und ein taeglicher Fehlalarm bringt dem Team bei, rote Builds zu
    # ignorieren. Wann die Datei zuletzt erzeugt wurde, weiss ohnehin git log.
    parts = [
        "# Architektur (automatisch erzeugt)",
        "",
        "> Diese Datei wird von `python -m data_api.architecture` aus der laufenden",
        "> App erzeugt. **Nicht von Hand bearbeiten** -- Aenderungen gehen beim",
        "> naechsten Lauf verloren. Das Konzept dahinter steht in",
        "> [`api_layer_concept.md`](api_layer_concept.md).",
        "",
        f"{len(arch.products)} Datenprodukte · "
        f"{len([r for r in arch.routes if not r.is_alias])} Routen",
        "",
        "## Datenfluss",
        "",
        "Von der Route über das Datenprodukt bis zur Datenquelle.",
        "⚠ markiert auslaufende Versionen.",
        "",
        "```mermaid",
        diagram_dataflow(arch),
        "```",
        "",
        "## Versionsstaende",
        "",
        "```mermaid",
        diagram_versions(arch),
        "```",
        "",
        "## Vertraege",
        "",
        "Die Felder, auf die sich die Dashboards verlassen.",
        "",
        "```mermaid",
        diagram_contracts(arch),
        "```",
        "",
        "## Routeninventar",
        "",
        "| Route | Methoden | Produkt | Version | Owner | Cache | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for route in arch.routes:
        product = route.product
        parts.append(
            f"| `{route.path}` | {', '.join(route.methods)} "
            f"| {product.name if product else '–'} "
            f"| {product.version if product else '–'} "
            f"| {product.owner if product else '–'} "
            f"| {f'{product.cache_ttl}s' if product else '–'} "
            f"| {'auslaufend' if route.deprecated else ('Alias' if route.is_alias else 'aktiv')} |"
        )

    parts += ["", "## Datenprodukte im Detail", ""]
    for info in arch.products:
        product = info.product
        parts += [
            f"### `{product.name}` v{product.major} ({product.version})",
            "",
            f"{product.summary}",
            "",
            f"* **Owner:** {product.owner}",
            f"* **Quellen:** {' + '.join(info.sources) or '–'}",
            f"* **Cache:** {product.cache_ttl}s",
            f"* **Filter:** {', '.join(f'`{f}`' for f in product.params_model.model_fields)}",
            f"* **Modul:** `{product.loader.__module__.replace('.', '/')}.py`",
            "",
        ]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# src/data_api/architecture.py -> src/data_api -> src -> api -> Repo-Wurzel
DEFAULT_OUT = Path(__file__).resolve().parents[3] / "docs" / "architecture.md"


def build() -> str:
    from data_api.application import create_app

    return render_markdown(collect(create_app()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"Zieldatei (Standard: {DEFAULT_OUT}).")
    parser.add_argument("--check", action="store_true",
                        help="Nur pruefen, ob die Datei aktuell ist (fuer CI).")
    args = parser.parse_args(argv)

    markdown = build()

    if args.check:
        vorhanden = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if vorhanden != markdown:
            print(f"{args.out} ist veraltet. Bitte erneut erzeugen:", file=sys.stderr)
            print("  python -m data_api.architecture", file=sys.stderr)
            return 1
        print(f"{args.out} ist aktuell.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(f"{args.out} geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
