"""
Welche Datenquellen braucht ein Datenprodukt?

Gelesen aus dem Quelltext des Loaders: welche `sources.X(...)` ruft er auf.
Per AST und nicht per Regex, damit ein `sources.neo4j` in einem Kommentar oder
einer Zeichenkette nicht mitzaehlt.

Warum abgeleitet statt deklariert? Ein Feld `sources=("neo4j",)` am
DataProduct waere einfacher zu lesen, koennte aber vom tatsaechlichen Code
abweichen -- jemand ergaenzt eine Postgres-Abfrage und vergisst das Feld. So
kann es nicht auseinanderlaufen.

Zwei Stellen nutzen das:
  * /readyz     prueft nur die Quellen, die wirklich gebraucht werden
  * architecture.py  zeichnet die Kanten Produkt -> Quelle
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any


def sources_used_by(loader: Any) -> list[str]:
    """Die Methodennamen, die der Loader auf seinem ersten Parameter aufruft.

        async def load(sources, params):
            await sources.neo4j(CYPHER)       ->  ["neo4j"]
            await sources.postgres(SQL, ...)  ->  ["neo4j", "postgres"]
    """
    try:
        quelltext = textwrap.dedent(inspect.getsource(loader))
    except (OSError, TypeError):
        return []

    knoten = ast.parse(quelltext).body[0]
    if not isinstance(knoten, ast.AsyncFunctionDef | ast.FunctionDef):
        return []
    if not knoten.args.args:
        return []

    parameter = knoten.args.args[0].arg
    gefunden = {
        n.func.attr
        for n in ast.walk(knoten)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == parameter
    }
    return sorted(gefunden)


def required_sources() -> set[str]:
    """Alle Quellen, die irgendein registriertes Datenprodukt braucht."""
    from data_api.products.registry import registry

    return {q for produkt in registry.all() for q in sources_used_by(produkt.loader)}
