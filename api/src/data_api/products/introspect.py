"""
Which data sources does a data product need?

Read from the loader's source code: which `sources.X(...)` does it call. Via the
AST rather than a regular expression, so a `sources.neo4j` inside a comment or a
string does not count.

Why derived instead of declared? A field like `sources=("neo4j",)` on the
DataProduct would be easier to read but could drift from the actual code --
somebody adds a Postgres query and forgets the field. This way it cannot.

Two places use it:
  * /readyz          checks only the sources that are actually needed
  * architecture.py  draws the product -> source edges
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any


def sources_used_by(loader: Any) -> list[str]:
    """The method names the loader calls on its first parameter.

        async def load(sources, params):
            await sources.neo4j(CYPHER)       ->  ["neo4j"]
            await sources.postgres(SQL, ...)  ->  ["neo4j", "postgres"]
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

    parameter = node.args.args[0].arg
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
    from data_api.products.registry import registry

    return {s for product in registry.all() for s in sources_used_by(product.loader)}
