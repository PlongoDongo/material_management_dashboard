"""
Generates the architecture documentation from the RUNNING app.

    python -m architecture --out ../docs/architecture.md
    python -m architecture --check          # CI: fails when stale

Why hand-built instead of an off-the-shelf package?

Generic tools (fastapi-router-viz, fastapi-di-viz, pydeps) analyse either the
source code or the dependency chain. Neither is enough here:

  * Our data product routes DO NOT EXIST in the source code -- they are created
    at runtime from the registry. A static parser simply cannot see them.
  * Every route hangs off the same dependency (`get_sources`). A DI graph
    therefore looks identical for every route and says nothing about WHICH
    source a product actually uses.

This file, by contrast, knows what those tools would have to guess:
version, owner, cache TTL, deprecation, contract fields -- it is all in the
registry already. The one missing piece (which product uses which source) is
read from the loader via the AST (products/introspect.py) instead of being
maintained by hand.

The consequence: the document cannot go stale. Adding a data product or
switching a source changes it automatically -- and `--check` makes sure nobody
forgets to regenerate it.
"""
from __future__ import annotations

import argparse
import ast
import inspect
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from products.base import DataProduct
from products.introspect import sources_used_by

# ---------------------------------------------------------------------------
# Collecting (introspection)
# ---------------------------------------------------------------------------

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class RouteInfo:
    path: str
    methods: list[str]
    summary: str
    tags: list[str]
    deprecated: bool
    product: DataProduct | None = None
    is_alias: bool = False
    # Hand-written write routes only. All three are DERIVED, never maintained:
    # the roles and the invalidated products from the route's dependencies, the
    # sources from the handler's body via the AST.
    required_groups: list[str] = field(default_factory=list)
    invalidates: list[str] = field(default_factory=list)
    writes_to: list[str] = field(default_factory=list)

    @property
    def is_write(self) -> bool:
        return bool(_WRITE_METHODS & set(self.methods))


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


def _parse_function(obj: Callable[..., Any]) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    """Parses a function into an AST. Decorators do not get in the way."""
    try:
        source = textwrap.dedent(inspect.getsource(obj))
    except (OSError, TypeError):
        return None
    node = ast.parse(source).body[0]
    return node if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) else None


def _write_declarations() -> dict[str, dict[str, list[str]]]:
    """What each hand-written write route declares, keyed by full path.

    Read from the TOPIC_ROUTERS rather than from the app: `include_router`
    turns a router into a private `_IncludedRouter` whose `APIRoute` objects the
    app no longer exposes. The routers themselves stay reachable, and that is
    where the dependencies live.

    Nothing here is maintained by hand -- the roles come from `requires(...)`,
    the invalidated products from `invalidates(...)`, and the sources from the
    handler body. Change the route and the table follows.
    """
    from fastapi.routing import APIRoute

    from api.v1 import API_V1_PREFIX, TOPIC_ROUTERS

    declarations: dict[str, dict[str, list[str]]] = {}
    for router in TOPIC_ROUTERS:
        for route in router.routes:
            if not isinstance(route, APIRoute) or not _WRITE_METHODS & route.methods:
                continue
            roles: list[str] = []
            invalidates: list[str] = []
            for dependency in route.dependencies:
                roles += list(getattr(dependency.dependency, "required_groups", ()))
                invalidates += list(getattr(dependency.dependency, "invalidated_products", ()))
            declarations[f"{API_V1_PREFIX}{route.path}"] = {
                "required_groups": sorted(set(roles)),
                "invalidates": sorted(set(invalidates)),
                "writes_to": sources_used_by(route.endpoint),
            }
    return declarations


def collect(app: FastAPI) -> Architecture:
    from products.registry import registry

    write_declarations = _write_declarations()

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

        declared = write_declarations.get(path, {})
        routes.append(
            RouteInfo(
                path=path,
                methods=methods,
                summary=operation.get("summary", ""),
                tags=[str(t) for t in operation.get("tags", [])],
                deprecated=bool(operation.get("deprecated", False)),
                product=product,
                is_alias=is_alias,
                required_groups=declared.get("required_groups", []),
                invalidates=declared.get("invalidates", []),
                writes_to=declared.get("writes_to", []),
            )
        )
    routes.sort(key=lambda r: r.path)
    return Architecture(routes=routes, products=products)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

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
        "> `python -m architecture`. **Do not edit by hand** -- changes are",
        "> lost on the next run. The reasoning behind the design is in",
        "> [`api_layer_concept.md`](api_layer_concept.md).",
        "",
        (f"{len(arch.products)} data products · "
         f"{len([r for r in arch.routes if not r.is_alias])} routes"),
        "",
        "## Contracts",
        "",
        "The fields the dashboards rely on.",
        "",
        "```mermaid",
        diagram_contracts(arch),
        "```",
        "",
        "## Write routes",
        "",
        "Hand-written, not generated -- a write is an ACTION with preconditions,",
        "a status code of its own and side effects, which a generator cannot",
        "usefully produce (see `api/v1/mappings.py`). What it CAN do is make sure",
        "nothing is forgotten: the role and the invalidation below are declared as",
        "route dependencies, `tests/test_architecture.py` fails the build if either",
        "is missing, and this table is read back off those same declarations.",
        "",
        "**Writes to** is read from the handler body via the AST -- empty means the",
        "route does not touch a data source yet. **Invalidates** names the read",
        "products whose cached answer this write makes stale; forgetting one shows",
        "the user the old value and makes them believe the save failed.",
        "",
        "| Route | Method | Role | Writes to | Invalidates |",
        "|---|---|---|---|---|",
    ]
    write_routes = [r for r in arch.routes if r.is_write]
    if write_routes:
        for route in write_routes:
            parts.append(
                f"| `{route.path}` | {', '.join(route.methods)} "
                f"| {', '.join(route.required_groups) or '–'} "
                f"| {', '.join(route.writes_to) or '–'} "
                f"| {', '.join(route.invalidates) or '–'} |"
            )
    else:
        parts.append("| – | – | – | – | – |")
    parts += [
        "",
        "## Route inventory",
        "",
        "| Route | Methods | Product | Version | Owner | Cache | Status | Sunset |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for route in arch.routes:
        product = route.product
        parts.append(
            f"| `{route.path}` | {', '.join(route.methods)} "
            f"| {product.name if product else '–'} "
            f"| {product.version if product else '–'} "
            f"| {product.owner if product else '–'} "
            f"| {f'{product.cache_ttl}s' if product else '–'} "
            f"| {'retiring' if route.deprecated else ('alias' if route.is_alias else 'active')} "
            f"| {product.sunset if product and product.sunset else '–'} |"
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

# src/architecture.py -> src -> api -> repo root
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "docs" / "architecture.md"


def build() -> str:
    from app import create_app

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
            print("  python -m architecture", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(f"{args.out} written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
