"""Tests of the filter logic (pure, without Dash/DB)."""
import polars as pl
import pytest

from data.filtering import apply_filters, normalize_filters, EMPTY_FILTERS


@pytest.fixture
def df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "material_number": ["MAT-1", "MAT-2", "MAT-3", "MAT-4"],
            "description": ["Dichtring", "Schraube", "Kabel", "Ölfilter"],
            "material_group": ["Rohstoffe", "", "Verpackung", None],
            "plant": ["Werk Köln", "Werk Berlin", "Werk Köln", "Werk Hamburg"],
            "status": ["Aktiv", "Gesperrt", "Aktiv", "Obsolet"],
            "einheit": ["M", "ST", "M", "L"],
            "stock": [100, 200, 300, 400],
            "changed_on": ["01.01.2026"] * 4,
        }
    )


def test_empty_filter_returns_all(df: pl.DataFrame) -> None:
    assert apply_filters(df, EMPTY_FILTERS).height == 4
    assert apply_filters(df, None).height == 4


def test_status_filter(df: pl.DataFrame) -> None:
    out = apply_filters(df, {"status": ["Aktiv"]})
    assert out.height == 2
    assert set(out["material_number"].to_list()) == {"MAT-1", "MAT-3"}


def test_multi_status_filter(df: pl.DataFrame) -> None:
    out = apply_filters(df, {"status": ["Aktiv", "Gesperrt"]})
    assert out.height == 3


def test_plant_filter(df: pl.DataFrame) -> None:
    assert apply_filters(df, {"plant": ["Werk Köln"]}).height == 2


def test_material_group_filter(df: pl.DataFrame) -> None:
    assert apply_filters(df, {"material_group": ["Verpackung"]}).height == 1


def test_unclassified_filter(df: pl.DataFrame) -> None:
    """Catches empty AND null material groups."""
    out = apply_filters(df, {"ohne_klass": True})
    assert out.height == 2
    assert set(out["material_number"].to_list()) == {"MAT-2", "MAT-4"}


def test_search_case_insensitive(df: pl.DataFrame) -> None:
    assert apply_filters(df, {"search": "dichtring"}).height == 1
    assert apply_filters(df, {"search": "MAT-"}).height == 4


def test_combined_filters(df: pl.DataFrame) -> None:
    out = apply_filters(df, {"status": ["Aktiv"], "plant": ["Werk Köln"]})
    assert out.height == 2


def test_normalize_fills_defaults() -> None:
    n = normalize_filters({"status": ["Aktiv"]})
    assert n["plant"] == [] and n["search"] == "" and n["ohne_klass"] is False
