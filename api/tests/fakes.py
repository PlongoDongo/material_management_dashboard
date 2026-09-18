"""
Test doubles for the data layer.

The sample data lives here -- NOT in src/. The difference:

  src/    is shipped. A fallback dataset in the production path can become
          active in production and serve invented numbers. There is none.
  tests/  is never shipped. These doubles stay for good, because the tests have
          to keep working without a running database.

`FakeSources` replaces `Sources` via `dependency_overrides` (see conftest.py).
It maps the QUERY to the rows it answers with -- the queries are imported
directly from the catalog files, so there are no copied strings that can drift.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Any

from products.catalog import example_1_plain as ex1
from products.catalog import example_2_paged as ex2
from products.catalog import example_3_filtered as ex3
from products.catalog import example_4_full as ex4
from products.catalog import material_overview_v2 as mo2
from products.catalog import material_overview_v3 as mo3
from products.catalog import material_search_v1 as ms1
from products.catalog import supplier_risk_v2 as sr2

# Fixed seed -> reproducible data, so tests can assert exact values.
_MATERIAL_GROUPS = ["Betriebsstoffe", "Rohstoffe", "Fertigerzeugnisse", "Verpackung",
                    "Ersatzteile", "Halbfabrikate", ""]
_PLANTS = [("W-KOE", "Werk Koeln"), ("W-BER", "Werk Berlin"),
           ("W-MUC", "Werk Muenchen"), ("W-HAM", "Werk Hamburg")]
_STATUSES = ["Aktiv", "Nicht geliefert", "Obsolet", "Gesperrt"]
_UNITS = ["M", "KG", "L", "PAK", "ST"]
_DESCRIPTIONS = ["Gewindestange M10", "Sensorhalter Typ B", "Dichtungsring NBR 25",
                 "Aluminiumprofil 40x40", "Steckverbinder 4-pol", "Oelfilter Standard",
                 "Edelstahlschraube M8x40", "Fuehrungsschiene 500mm", "Zahnriemen HTD-5M"]
_SUPPLIERS = [("L-001", "Nordstahl GmbH", "DE"), ("L-002", "Alpine Precision AG", "AT"),
              ("L-003", "Baltic Components OY", "FI"), ("L-004", "Iberia Metals SL", "ES")]


def _material_base(n: int = 64) -> list[dict[str, Any]]:
    rng = random.Random(42)
    rows = []
    for i in range(n):
        plant_id, plant_name = rng.choice(_PLANTS)
        rows.append({
            "material_number": f"MAT-{100777 + i * 13}",
            "description": rng.choice(_DESCRIPTIONS),
            "material_group": rng.choices(_MATERIAL_GROUPS, weights=[18, 18, 18, 12, 12, 16, 6])[0],
            "plant_id": plant_id,
            "plant_name": plant_name,
            "status": rng.choices(_STATUSES, weights=[55, 18, 15, 12])[0],
            "unit": rng.choice(_UNITS),
            "stock": rng.randint(300, 9800),
            "price": round(rng.uniform(0.5, 480.0), 2),
            "changed_on": f"2026-{rng.randint(1, 8):02d}-{rng.randint(1, 28):02d}",
        })
    return rows


def material_rows_v2() -> list[dict[str, Any]]:
    """Matches mo2.CYPHER: has `plant` and `unit`, no plant_id/price."""
    return [
        {k: v for k, v in row.items() if k not in ("plant_id", "plant_name", "price")}
        | {"plant": row["plant_name"]}
        for row in _material_base()
    ]


def material_rows_v3() -> list[dict[str, Any]]:
    """Matches mo3.CYPHER: has plant_id/plant_name and price, no unit."""
    return [{k: v for k, v in row.items() if k != "unit"} for row in _material_base()]


# --- material-search: the fake has to behave like a database ----------------
#
# Every other product here answers with a fixed row list, because the product
# does its filtering in Python and the test can check the outcome. material-search
# pushes filtering, ordering and the window INTO the query, so a fixed answer
# would test nothing -- the whole point is what the database does with the
# parameters. So this emulates it: filter, sort, slice.
#
# That makes these two functions a second implementation of the same rules, and
# a second implementation can drift from the first. The backstop is
# test_integration_neo4j.py, which runs the real query against a real Neo4j when
# one is available (NEO4J_TEST_URI). Believe that one over this one.

def _search_filtered(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """The WHERE clause of ms1._MATCH_AND_FILTER, in Python."""
    rows = _material_base()
    if parameters.get("status"):
        rows = [r for r in rows if r["status"] in parameters["status"]]
    if parameters.get("plant_id"):
        rows = [r for r in rows if r["plant_id"] in parameters["plant_id"]]
    if parameters.get("material_group"):
        rows = [r for r in rows if r["material_group"] in parameters["material_group"]]
    if parameters.get("unclassified_only"):
        rows = [r for r in rows if not r["material_group"]]
    if parameters.get("min_stock") is not None:
        rows = [r for r in rows if r["stock"] >= parameters["min_stock"]]
    if parameters.get("search"):
        needle = parameters["search"]
        rows = [r for r in rows
                if needle in f"{r['material_number']} {r['description']}".lower()]
    return rows


def search_page(cypher: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """ORDER BY + SKIP/LIMIT of ms1.CYPHER_PAGE, in Python.

    The sort column is read back out of the query text because that is where the
    product put it -- Cypher cannot parameterise a property name, so the fake
    cannot receive it as a parameter either.
    """
    rows = _search_filtered(parameters)
    if "m.bestand DESC" in cypher:
        rows.sort(key=lambda r: (-r["stock"], r["material_number"]))
    elif "m.geaendert DESC" in cypher:
        rows.sort(key=lambda r: (r["changed_on"], r["material_number"]), reverse=True)
    else:
        rows.sort(key=lambda r: r["material_number"])

    offset = parameters.get("offset", 0)
    return rows[offset: offset + parameters.get("limit", len(rows))]


def search_total(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """ms1.CYPHER_COUNT: the number of matches BEFORE the window."""
    return [{"total": len(_search_filtered(parameters))}]


# --- The four templates in catalog/example_*.py -----------------------------
#
# All four query the same Material nodes with the same two optional filters, so
# ONE handler serves them. Writing four bespoke ones would mean four chances for
# the fake to disagree with a template it is supposed to demonstrate.

def example_rows(cypher: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter, order and window -- whatever the query in question asks for."""
    rows = _material_base()
    if parameters.get("status"):
        rows = [r for r in rows if r["status"] in parameters["status"]]
    if parameters.get("min_stock") is not None:
        rows = [r for r in rows if r["stock"] >= parameters["min_stock"]]

    # The sort column is read back out of the query text: Cypher cannot take a
    # property name as a parameter, so the fake cannot receive it as one either.
    if "m.bestand DESC" in cypher:
        rows.sort(key=lambda r: (-r["stock"], r["material_number"]))
    else:
        rows.sort(key=lambda r: r["material_number"])

    if "SKIP $offset" in cypher:
        offset = parameters.get("offset", 0)
        rows = rows[offset: offset + parameters.get("limit", len(rows))]
    return rows


def example_total(cypher: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """The COUNT queries: how many rows match BEFORE the window."""
    counting = cypher.replace("SKIP $offset LIMIT $limit", "")
    return [{"total": len(example_rows(counting, parameters))}]


def supplier_rows() -> list[dict[str, Any]]:
    """Matches sr2.CYPHER."""
    rng = random.Random(7)
    return [
        {"supplier_id": sid, "supplier_name": name, "country": country,
         "material_count": rng.randint(3, 40)}
        for sid, name, country in _SUPPLIERS
    ]


def delivery_rows(since: dt.date) -> list[dict[str, Any]]:
    """Matches sr2.SQL. Suppliers have deliberately different reliability so the
    risk score in the data product spreads visibly."""
    rng = random.Random(11)
    rows = []
    for supplier, delay_bias in (("L-001", 0), ("L-002", 2), ("L-003", 6), ("L-004", 1)):
        for i in range(40):
            promised = dt.date(2026, 1, 1) + dt.timedelta(days=i * 5)
            delivered = promised + dt.timedelta(days=max(0, int(rng.gauss(delay_bias, 3))))
            if delivered < since:
                continue
            rows.append({
                "supplier_id": supplier,
                "material_number": f"MAT-{100777 + rng.randint(0, 63) * 13}",
                "delivered_on": delivered,
                "promised_on": promised,
                "quantity": rng.randint(10, 900),
                "complaints": rng.choices([0, 1, 2], weights=[85, 12, 3])[0],
            })
    return rows


class FakeSources:
    """Replaces `Sources` -- no drivers, no sessions.

    The data products notice nothing: they call `await sources.neo4j(...)` and
    get rows back, exactly as in production.
    """

    def __init__(self) -> None:
        self.used: set[str] = set()
        # Every call as (query, parameters). ONE list instead of separate
        # collections, so the association stays unambiguous: which value went to
        # WHICH query? For a product with two sources (supplier-risk) a shared
        # dict would silently overwrite identically named parameters.
        #
        # This is the seam for filters that live in the query: the fake does NOT
        # apply them -- it would otherwise reimplement Cypher in Python and the
        # test would end up checking the fake. Whether a filter really filters
        # belongs in tests/test_integration_neo4j.py against a real database.
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # What the ORM write path handed over (see Sources.add).
        self.added: list[Any] = []

    @property
    def queries(self) -> list[str]:
        """Just the query texts, in call order."""
        return [query for query, _ in self.calls]

    @property
    def parameters(self) -> dict[str, Any]:
        """All parameters merged -- convenient, but source-blind.

        A test that needs to be precise (two sources, same parameter name) uses
        `fake.calls[0]` instead of this shortcut.
        """
        return {name: value for _, p in self.calls for name, value in p.items()}

    # ANN401 on both methods: these signatures MIRROR db/sources.py on purpose.
    # A narrower type here would let a test pass that the real Sources rejects.
    # ANN401 on both methods: these signatures MIRROR db/sources.py on purpose.
    # A narrower type here would let a test pass that the real Sources rejects.
    async def neo4j(self, cypher: str, **parameters: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        self.used.add("neo4j")
        self.calls.append((cypher, parameters))
        if cypher is mo2.CYPHER:
            return material_rows_v2()
        if cypher is mo3.CYPHER:
            return material_rows_v3()
        if cypher is sr2.CYPHER:
            return supplier_rows()
        # material-search builds its page query with .format(order_by=...), so
        # there is no object to compare by identity. The COUNT query is checked
        # first because both start with the same shared fragment.
        if cypher is ms1.CYPHER_COUNT:
            return search_total(parameters)
        if cypher.startswith(ms1._MATCH_AND_FILTER):
            return search_page(cypher, parameters)
        # The templates. COUNT queries are checked first because two of them
        # share their opening fragment with the matching page query.
        if cypher in (ex2.CYPHER_COUNT, ex4.CYPHER_COUNT):
            return example_total(cypher, parameters)
        if cypher in (ex1.CYPHER, ex2.CYPHER_PAGE, ex3.CYPHER) or cypher.startswith(
            ex4._MATCH_AND_FILTER
        ):
            return example_rows(cypher, parameters)
        raise AssertionError(
            "FakeSources does not know this Cypher query. New data product? "
            "Then add a matching answer in tests/fakes.py.\n\n" + cypher
        )

    async def postgres(self, sql: str, **parameters: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        self.used.add("postgres")
        self.calls.append((sql, parameters))
        if sql is sr2.SQL:
            return delivery_rows(parameters["since"])
        raise AssertionError(
            "FakeSources does not know this SQL query. New data product? "
            "Then add a matching answer in tests/fakes.py.\n\n" + sql
        )

    # ANN401: mirrors Sources.add, which takes whatever db/models.py declares.
    async def add(self, *rows: Any) -> None:  # noqa: ANN401
        """Records the objects instead of writing them.

        The real `add()` also reads back what the database generated. The fake
        fills in `created_at` for the same reason: otherwise a route returning
        it would fail for a reason that has nothing to do with the route.
        """
        self.used.add("postgres")
        self.added.extend(rows)
        for row in rows:
            if getattr(row, "created_at", None) is None:
                # DTZ001: the column is TIMESTAMP WITHOUT TIME ZONE, so the
                # value the database returns is naive too.
                row.created_at = dt.datetime(2026, 9, 17, 8, 30)  # noqa: DTZ001

    async def commit(self) -> None:
        """No-op -- there is no transaction to commit."""

    @property
    def label(self) -> str:
        return "+".join(sorted(self.used)) or "none"
