"""
Which data sources does a data product need?

Read from the loader's source code: which `sources.X(...)` does it call. Via the
AST rather than a regular expression, so a `sources.neo4j` inside a comment or a
string does not count.

Why derived instead of declared? A field like `sources=("neo4j",)` on the
DataProduct would be easier to read but could drift from the actual code --
somebody adds a Postgres query and forgets the field. This way it cannot.

Two places use it (details in `sources_used_by`):
  * GET /readyz      counts a missing source as a problem only if a product needs it
  * architecture.py  lists the sources per data product and per write route
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable
from typing import Any


def sources_used_by(loader: Callable[..., Any]) -> list[str]:
    """The method names a function calls on its `sources` parameter.

        async def load(sources, params):
            await sources.neo4j(CYPHER)       ->  ["neo4j"]
            await sources.postgres(SQL, ...)  ->  ["neo4j", "postgres"]

    Who reads the result, and why -- both would otherwise need a list kept in
    step by hand:

      * GET /readyz, via `required_sources()`. A source no product queries is
        not a problem; one a product needs is. So a deployment that forgot
        SQL_HOST shows up in /readyz right after the rollout, instead of on
        the first dashboard click that happens to hit a Postgres product.
      * docs/architecture.md. "Sources" per data product answers "Postgres is
        down -- which dashboards are affected?", and "Writes to" shows where a
        hand-written write route writes.

    Works for data product loaders and for hand-written route handlers alike.
    """
    try:
        source_code = textwrap.dedent(inspect.getsource(loader))
    except (OSError, TypeError):
        return []

    node = ast.parse(source_code).body[0]
    if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
        return []
    if not node.args.args:
        return []

    # The parameter the calls are made on. A data product loader takes it
    # first (`load(sources, params)`), a hand-written write route usually does
    # not (`create_mapping(payload, sources, principal)`) -- so prefer the one
    # actually named `sources` and fall back to the first for anything that
    # names it differently.
    names = [argument.arg for argument in node.args.args]
    parameter = "sources" if "sources" in names else names[0]
    found = {
        n.func.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == parameter
    }
    return sorted(found)


def required_sources() -> set[str]:
    """Every source that any registered data product needs."""
    from products.registry import registry

    return {s for product in registry.all() for s in sources_used_by(product.loader)}
