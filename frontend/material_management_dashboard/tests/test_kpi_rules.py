"""Tests of the rule-based KPI calculation."""
import polars as pl
import pytest

from kpi.kpi_rules import (
    compute_kpis,
    count_active,
    count_blocked,
    count_obsolete,
    count_not_delivered,
    count_unclassified,
    KPI_DEFINITIONS,
)


@pytest.fixture
def df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "status": ["Aktiv", "Aktiv", "Gesperrt", "Obsolet", "Nicht geliefert"],
            "material_group": ["Rohstoffe", None, "", "Verpackung", "Ersatzteile"],
        }
    )


def test_count_status(df: pl.DataFrame) -> None:
    assert count_active(df) == 2
    assert count_blocked(df) == 1
    assert count_obsolete(df) == 1
    assert count_not_delivered(df) == 1


def test_count_unclassified_counts_empty_and_null(df: pl.DataFrame) -> None:
    # None + "" -> 2
    assert count_unclassified(df) == 2


def test_compute_kpis_shape(df: pl.DataFrame) -> None:
    kpis = compute_kpis(df)
    assert len(kpis) == len(KPI_DEFINITIONS)
    for k in kpis:
        assert {"id", "label", "color", "value", "filter"} <= k.keys()
        assert isinstance(k["value"], int)


def test_kpi_click_filters_are_valid() -> None:
    """Every KPI carries an applicable filter update."""
    for k in KPI_DEFINITIONS:
        assert "status" in k["filter"]
        assert "ohne_klass" in k["filter"]
