"""
Tests of the pure transformations -- no HTTP, no database, no app.

This is the fastest test level and the one that finds the most real bugs: the
domain logic lives here. The HTTP tests next door only check that the wiring is
correct.
"""
from __future__ import annotations

import datetime as dt

import neo4j.spatial as ns
import neo4j.time as nt
import pytest
from neo4j.graph import Graph, Node

from data_api.db.sources import _to_python_value
from data_api.products.catalog.material_overview_v3 import (
    MaterialParamsV3,
    transform as transform_material,
)
from data_api.products.catalog.supplier_risk_v2 import (
    SupplierRiskParams,
    transform as transform_risk,
)

RAW_ROWS = [
    {"material_number": "MAT-1", "description": "Schraube", "material_group": "Rohstoffe",
     "plant_id": "W-KOE", "plant_name": "Werk Koeln", "status": "Aktiv",
     "stock": 10, "price": 2.5, "changed_on": "2026-01-01"},
    {"material_number": "MAT-2", "description": "Mutter", "material_group": "",
     "plant_id": "W-BER", "plant_name": "Werk Berlin", "status": "Gesperrt",
     "stock": None, "price": 1.0, "changed_on": "2026-02-01"},
]


def test_stock_value_is_computed():
    rows = transform_material(RAW_ROWS, MaterialParamsV3())
    assert rows[0]["stock_value"] == 25.0


def test_missing_stock_stays_unknown_instead_of_zero():
    """Important: None != 0. A missing stock level is unknown, not empty."""
    rows = transform_material(RAW_ROWS, MaterialParamsV3())
    assert rows[1]["stock"] is None
    assert rows[1]["stock_value"] is None


def test_empty_material_group_is_normalised_to_none():
    rows = transform_material(RAW_ROWS, MaterialParamsV3())
    assert rows[1]["material_group"] is None


def test_unclassified_only_finds_empty_and_missing_groups():
    rows = transform_material(RAW_ROWS, MaterialParamsV3(unclassified_only=True))
    assert [r["material_number"] for r in rows] == ["MAT-2"]


def test_search_is_case_insensitive_over_number_and_description():
    assert len(transform_material(RAW_ROWS, MaterialParamsV3(search="schraube"))) == 1
    assert len(transform_material(RAW_ROWS, MaterialParamsV3(search="mat-"))) == 2


def test_min_stock_value_drops_rows_without_a_value():
    rows = transform_material(RAW_ROWS, MaterialParamsV3(min_stock_value=10))
    assert [r["material_number"] for r in rows] == ["MAT-1"]


# --- Risk score -------------------------------------------------------------

MASTER = [{"supplier_id": "S-1", "supplier_name": "Punctual Ltd", "country": "DE",
           "material_count": 5},
          {"supplier_id": "S-2", "supplier_name": "Late Inc", "country": "AT",
           "material_count": 3}]


def _delivery(supplier_id: str, delay: int, complaints: int = 0) -> dict:
    promised = dt.date(2026, 3, 1)
    return {"supplier_id": supplier_id, "material_number": "MAT-1",
            "promised_on": promised, "delivered_on": promised + dt.timedelta(days=delay),
            "quantity": 100, "complaints": complaints}


def test_punctual_supplier_scores_zero():
    rows = transform_risk(MASTER[:1], [_delivery("S-1", 0)], SupplierRiskParams())
    assert rows[0]["risk_score"] == 0.0
    assert rows[0]["on_time_rate_pct"] == 100.0
    assert rows[0]["risk_class"] == "low"


def test_delay_and_complaints_raise_the_score():
    deliveries = [_delivery("S-1", 0), _delivery("S-2", 14, complaints=1)]
    rows = transform_risk(MASTER, deliveries, SupplierRiskParams())
    by_id = {r["supplier_id"]: r for r in rows}
    assert by_id["S-2"]["risk_score"] > by_id["S-1"]["risk_score"]
    # 0.5*100 + 0.3*100 + 0.2*100 = 100 at maximum delay
    assert by_id["S-2"]["risk_score"] == 100.0
    assert by_id["S-2"]["risk_class"] == "high"


def test_tolerance_days_shift_the_on_time_boundary():
    deliveries = [_delivery("S-1", 2)]
    without = transform_risk(MASTER[:1], deliveries, SupplierRiskParams(tolerance_days=0))
    with_tol = transform_risk(MASTER[:1], deliveries, SupplierRiskParams(tolerance_days=3))
    assert without[0]["on_time_rate_pct"] == 0.0
    assert with_tol[0]["on_time_rate_pct"] == 100.0
    assert with_tol[0]["risk_score"] < without[0]["risk_score"]


def test_supplier_without_deliveries_is_hidden_by_default():
    rows = transform_risk(MASTER, [_delivery("S-1", 0)], SupplierRiskParams(min_deliveries=1))
    assert [r["supplier_id"] for r in rows] == ["S-1"]


def test_empty_input_yields_empty_output_instead_of_crashing():
    assert transform_risk([], [], SupplierRiskParams()) == []
    assert transform_risk(MASTER, [], SupplierRiskParams(min_deliveries=0)) != []


def test_supplier_without_deliveries_is_unknown_not_low_risk():
    """No data must not become a top grade.

    Before: fill_null(1.0) on "on_time" -> score 0.0 -> class "low". A supplier
    with no history at all sat at the bottom of the risk list. It went unnoticed
    only because of the default min_deliveries=1, which filtered those rows out
    again -- so correctness hung on the default of a DIFFERENT parameter.
    """
    rows = transform_risk(MASTER, [_delivery("S-1", 0)],
                          SupplierRiskParams(min_deliveries=0))
    by_id = {r["supplier_id"]: r for r in rows}

    assert by_id["S-1"]["risk_class"] == "low"        # has data
    unknown = by_id["S-2"]                             # has none
    assert unknown["deliveries"] == 0
    assert unknown["risk_score"] is None
    assert unknown["risk_class"] == "unknown"
    assert unknown["on_time_rate_pct"] is None


def test_rows_without_data_sort_last():
    rows = transform_risk(MASTER, [_delivery("S-1", 14, complaints=1)],
                          SupplierRiskParams(min_deliveries=0))
    assert [r["risk_class"] for r in rows] == ["high", "unknown"]


# --- Conversion of Neo4j-specific types -------------------------------------
# These tests cover a bug that would have hit on the first real date coming out
# of the graph: the driver returns its own classes, which Pydantic rejects.

def test_neo4j_date_becomes_a_python_date():
    assert _to_python_value(nt.Date(2026, 8, 20)) == dt.date(2026, 8, 20)
    assert _to_python_value(nt.DateTime(2026, 8, 20, 10, 30)) == dt.datetime(2026, 8, 20, 10, 30)
    assert _to_python_value(nt.Time(10, 30)) == dt.time(10, 30)


def test_duration_becomes_readable_text_not_a_bare_array():
    """Duration subclasses tuple and would silently have become [3,2,0,90]."""
    assert _to_python_value(nt.Duration(months=3, days=2, seconds=90)) == "P3M2DT1M30S"


def test_point_keeps_its_meaning_and_shape():
    """Point subclasses tuple too -- without srid it is unclear what 7.1 means.

    `z` is always present so the row shape does not change between a 2D and a 3D
    point in the same response.
    """
    assert _to_python_value(ns.WGS84Point((7.1, 50.7, 100.0))) == {
        "srid": 4979, "x": 7.1, "y": 50.7, "z": 100.0}
    assert _to_python_value(ns.CartesianPoint((1.0, 2.0))) == {
        "srid": 7203, "x": 1.0, "y": 2.0, "z": None}


def test_node_becomes_its_properties():
    node = Node(Graph(), "n1", "4:a:1", ["Material"], {"nr": "MAT-1", "bestand": 10})
    assert _to_python_value(node) == {"nr": "MAT-1", "bestand": 10}


def test_nested_values_are_converted_too():
    """collect() and map projections produce lists and dicts."""
    raw = {"plant": "Koeln", "dates": [nt.Date(2026, 1, 1), nt.Date(2026, 2, 1)],
           "detail": {"as_of": nt.Date(2026, 3, 1)}}
    assert _to_python_value(raw) == {
        "plant": "Koeln",
        "dates": [dt.date(2026, 1, 1), dt.date(2026, 2, 1)],
        "detail": {"as_of": dt.date(2026, 3, 1)},
    }


def test_unknown_driver_types_fail_loudly():
    """A driver type this function does not know must not pass through silently.

    The check has to run BEFORE the container handling: several Neo4j types
    subclass tuple and would otherwise turn into plain lists.
    """
    with pytest.raises(TypeError, match="Untranslatable Neo4j type"):
        _to_python_value(nt.ClockTime(1, 2))


def test_plain_values_are_unchanged():
    for value in ("text", 42, 3.14, True, None):
        assert _to_python_value(value) == value
