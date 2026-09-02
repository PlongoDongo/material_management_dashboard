"""
Generates the visual architecture documentation from the RUNNING app.

    python -m data_api.architecture --out ../docs/architecture.md
    python -m data_api.architecture --check          # CI: fails when stale

Why hand-built instead of an off-the-shelf package?

Generic tools (fastapi-router-viz, fastapi-di-viz, pydeps) analyse either the
source code or the dependency chain. Neither is enough here:

  * Our data product routes DO NOT EXIST in the source code -- they are created
    at runtime from the registry. A static parser simply cannot see them.
  * Every route hangs off the same dependency (`get_sources`). A DI graph
    therefore looks identical for every route and says nothing about WHICH
    source a product actually uses.

These 200 lines, by contrast, know what those tools would have to guess:
version, owner, cache TTL, deprecation, contract fields -- it is all in the
registry already. The one missing piece (which product uses which source) is
read from the loader via the AST (products/introspect.py) instead of being
maintained by hand.

The consequence: the diagram cannot go stale. Adding a data product or
switching a source changes the diagram automatically -- and `--check` makes sure
nobody forgets to regenerate it.
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
# Collecting (introspection)
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
    """Parses a function into an AST. Decorators do not get in the way."""
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

    # Read the routes from the OpenAPI schema, not from `app.routes`.
    # The reason: FastAPI keeps included routers internally as `_IncludedRouter`
    # -- a private structure that changes between versions (which is exactly
    # what happened while building this). The OpenAPI schema, by contrast, is
    # the app's public, stable contract and contains everything we need: path,
    # methods, summary, tags, deprecated flag.
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
# Rendering (Mermaid)
# ---------------------------------------------------------------------------

def _id(*parts: str) -> str:
    """Mermaid node ids must not contain special characters."""
    raw = "_".join(parts)
    return "".join(c if c.isalnum() or c == "_" else "_" for c in raw)


def diagram_dataflow(arch: Architecture) -> str:
    """The main diagram: route -> data product -> data source."""
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
            continue          # the alias points at the same version -- duplicate edges
        label = route.path.replace("/api/v1", "")
        marker = " ⚠" if route.deprecated else ""
        lines.append(f'    {_id("r", route.path)}["{"/".join(m for m in route.methods)} '
                     f'{label}{marker}"]')
    lines.append("  end")
    lines.append("")

    lines.append('  subgraph products["Data products"]')
    for info in arch.products:
        node = _id("p", info.product.name, str(info.product.major))
        marker = " ⚠" if info.product.deprecated else ""
        lines.append(f'    {node}["{info.product.name}<br/>v{info.product.major} '
                     f'· {info.product.version}{marker}"]')
    lines.append("  end")
    lines.append("")

    all_sources = sorted({s for info in arch.products for s in info.sources})
    lines.append('  subgraph sources["Data sources"]')
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
    """Version states per product family -- what is live, what is being retired."""
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
            status = "retiring" if product.deprecated else "active"
            sunset = f"<br/>Sunset {product.sunset}" if product.sunset else ""
            lines.append(f'    {node}["v{product.major} · {product.version}'
                         f'<br/>{status}{sunset}"]')
            if previous:
                lines.append(f"    {previous} -.->|superseded by| {node}")
            previous = node
        lines.append("  end")
    return "\n".join(lines)


def diagram_contracts(arch: Architecture) -> str:
    """The contracts themselves -- which fields each version returns."""
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
    # DELIBERATELY no timestamp: the content of this file must be a pure
    # function of the code. With today's date in it, the staleness check
    # (tests/test_architecture.py) would fail every day without anything having
    # changed -- and a daily false alarm teaches a team to ignore red builds.
    # When the file was last generated is what git log is for.
    parts = [
        "# Architecture (generated)",
        "",
        "> This file is generated from the running app by",
        "> `python -m data_api.architecture`. **Do not edit by hand** -- changes are",
        "> lost on the next run. The reasoning behind the design is in",
        "> [`api_layer_concept.md`](api_layer_concept.md).",
        "",
        f"{len(arch.products)} data products · "
        f"{len([r for r in arch.routes if not r.is_alias])} routes",
        "",
        "## Data flow",
        "",
        "From the route through the data product to the data source.",
        "⚠ marks versions that are being retired.",
        "",
        "```mermaid",
        diagram_dataflow(arch),
        "```",
        "",
        "## Version states",
        "",
        "```mermaid",
        diagram_versions(arch),
        "```",
        "",
        "## Contracts",
        "",
        "The fields the dashboards rely on.",
        "",
        "```mermaid",
        diagram_contracts(arch),
        "```",
        "",
        "## Route inventory",
        "",
        "| Route | Methods | Product | Version | Owner | Cache | Status |",
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
            f"| {'retiring' if route.deprecated else ('alias' if route.is_alias else 'active')} |"
        )

    parts += ["", "## Data products in detail", ""]
    for info in arch.products:
        product = info.product
        parts += [
            f"### `{product.name}` v{product.major} ({product.version})",
            "",
            f"{product.summary}",
            "",
            f"* **Owner:** {product.owner}",
            f"* **Sources:** {' + '.join(info.sources) or '–'}",
            f"* **Cache:** {product.cache_ttl}s",
            f"* **Filters:** {', '.join(f'`{f}`' for f in product.params_model.model_fields)}",
            f"* **Module:** `{product.loader.__module__.replace('.', '/')}.py`",
            "",
        ]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# src/data_api/architecture.py -> src/data_api -> src -> api -> repo root
DEFAULT_OUT = Path(__file__).resolve().parents[3] / "docs" / "architecture.md"


def build() -> str:
    from data_api.application import create_app

    return render_markdown(collect(create_app()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"Output file (default: {DEFAULT_OUT}).")
    parser.add_argument("--check", action="store_true",
                        help="Only check whether the file is current (for CI).")
    args = parser.parse_args(argv)

    markdown = build()

    if args.check:
        vorhanden = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if vorhanden != markdown:
            print(f"{args.out} is out of date. Please regenerate:", file=sys.stderr)
            print("  python -m data_api.architecture", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(f"{args.out} written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
