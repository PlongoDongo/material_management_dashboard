"""Tests für die regelbasierte KPI-Berechnung."""
import polars as pl
import pytest

from kpi.kpi_rules import (
    compute_kpis,
    count_aktiv,
    count_gesperrt,
    count_obsolet,
    count_nicht_geliefert,
    count_ohne_klassifizierung,
    KPI_DEFINITIONS,
)


@pytest.fixture
def df():
    return pl.DataFrame(
        {
            "status": ["Aktiv", "Aktiv", "Gesperrt", "Obsolet", "Nicht geliefert"],
            "warengruppe": ["Rohstoffe", None, "", "Verpackung", "Ersatzteile"],
        }
    )


def test_count_status(df):
    assert count_aktiv(df) == 2
    assert count_gesperrt(df) == 1
    assert count_obsolet(df) == 1
    assert count_nicht_geliefert(df) == 1


def test_count_ohne_klassifizierung(df):
    # None + "" -> 2
    assert count_ohne_klassifizierung(df) == 2


def test_compute_kpis_shape(df):
    kpis = compute_kpis(df)
    assert len(kpis) == len(KPI_DEFINITIONS)
    for k in kpis:
        assert {"id", "label", "color", "value", "filter"} <= k.keys()
        assert isinstance(k["value"], int)


def test_kpi_click_filters_are_valid():
    """Jede KPI trägt ein anwendbares Filter-Update."""
    for k in KPI_DEFINITIONS:
        assert "status" in k["filter"]
        assert "ohne_klass" in k["filter"]
