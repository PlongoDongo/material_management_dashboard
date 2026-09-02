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

from data_api.products.catalog import material_overview_v2 as mo2
from data_api.products.catalog import material_overview_v3 as mo3
from data_api.products.catalog import supplier_risk_v2 as sr2

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

    async def neo4j(self, cypher: str, **parameters: Any) -> list[dict[str, Any]]:
        self.used.add("neo4j")
        self.calls.append((cypher, parameters))
        if cypher is mo2.CYPHER:
            return material_rows_v2()
        if cypher is mo3.CYPHER:
            return material_rows_v3()
        if cypher is sr2.CYPHER:
            return supplier_rows()
        raise AssertionError(
            "FakeSources does not know this Cypher query. New data product? "
            "Then add a matching answer in tests/fakes.py.\n\n" + cypher
        )

    async def postgres(self, sql: str, **parameters: Any) -> list[dict[str, Any]]:
        self.used.add("postgres")
        self.calls.append((sql, parameters))
        if sql is sr2.SQL:
            return delivery_rows(parameters["since"])
        raise AssertionError(
            "FakeSources does not know this SQL query. New data product? "
            "Then add a matching answer in tests/fakes.py.\n\n" + sql
        )

    async def commit(self) -> None:
        """No-op -- there is no transaction to commit."""

    @property
    def label(self) -> str:
        return "+".join(sorted(self.used)) or "none"
