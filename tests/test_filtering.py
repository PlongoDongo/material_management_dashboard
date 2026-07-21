"""Tests für die Filterlogik (rein, ohne Dash/DB)."""
import polars as pl
import pytest

from data.filtering import apply_filters, normalize_filters, EMPTY_FILTERS


@pytest.fixture
def df():
    return pl.DataFrame(
        {
            "material_nr": ["MAT-1", "MAT-2", "MAT-3", "MAT-4"],
            "bezeichnung": ["Dichtring", "Schraube", "Kabel", "Ölfilter"],
            "warengruppe": ["Rohstoffe", "", "Verpackung", None],
            "werk": ["Werk Köln", "Werk Berlin", "Werk Köln", "Werk Hamburg"],
            "status": ["Aktiv", "Gesperrt", "Aktiv", "Obsolet"],
            "einheit": ["M", "ST", "M", "L"],
            "bestand": [100, 200, 300, 400],
            "geaendert": ["01.01.2026"] * 4,
        }
    )


def test_empty_filter_returns_all(df):
    assert apply_filters(df, EMPTY_FILTERS).height == 4
    assert apply_filters(df, None).height == 4


def test_status_filter(df):
    out = apply_filters(df, {"status": ["Aktiv"]})
    assert out.height == 2
    assert set(out["material_nr"].to_list()) == {"MAT-1", "MAT-3"}


def test_multi_status_filter(df):
    out = apply_filters(df, {"status": ["Aktiv", "Gesperrt"]})
    assert out.height == 3


def test_werk_filter(df):
    assert apply_filters(df, {"werk": ["Werk Köln"]}).height == 2


def test_warengruppe_filter(df):
    assert apply_filters(df, {"warengruppe": ["Verpackung"]}).height == 1


def test_ohne_klassifizierung(df):
    """Erfasst leere UND null-Warengruppen."""
    out = apply_filters(df, {"ohne_klass": True})
    assert out.height == 2
    assert set(out["material_nr"].to_list()) == {"MAT-2", "MAT-4"}


def test_search_case_insensitive(df):
    assert apply_filters(df, {"search": "dichtring"}).height == 1
    assert apply_filters(df, {"search": "MAT-"}).height == 4


def test_combined_filters(df):
    out = apply_filters(df, {"status": ["Aktiv"], "werk": ["Werk Köln"]})
    assert out.height == 2


def test_normalize_fills_defaults():
    n = normalize_filters({"status": ["Aktiv"]})
    assert n["werk"] == [] and n["search"] == "" and n["ohne_klass"] is False
