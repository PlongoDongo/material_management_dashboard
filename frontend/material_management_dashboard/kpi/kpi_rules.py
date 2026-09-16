"""
Rule-based KPI calculation.

Every KPI has:
  * a pure function `value_fn(df) -> int` that computes the value from the
    COMPLETE dataset (the KPIs show the overall picture, independent of the
    table filter currently in effect),
  * a `filter` dict that is applied when the tile is clicked.

The functions are deliberately pure (Polars in, number out) so that they
are testable in PyTest without Dash or a database.
"""
from __future__ import annotations

import polars as pl

from config import KPI_COLORS


# --------------------------------------------------------------------------
# Pure calculation rules  (df is a Polars DataFrame)
# --------------------------------------------------------------------------
def _count_status(df: pl.DataFrame, status: str) -> int:
    return int(df.filter(pl.col("status") == status).height)


def count_active(df: pl.DataFrame) -> int:
    return _count_status(df, "Aktiv")


def count_not_delivered(df: pl.DataFrame) -> int:
    return _count_status(df, "Nicht geliefert")


def count_obsolete(df: pl.DataFrame) -> int:
    return _count_status(df, "Obsolet")


def count_blocked(df: pl.DataFrame) -> int:
    return _count_status(df, "Gesperrt")


def count_unclassified(df: pl.DataFrame) -> int:
    """Materials without a material group (null or empty)."""
    return int(
        df.filter(
            pl.col("material_group").is_null() | (pl.col("material_group") == "")
        ).height
    )


# --------------------------------------------------------------------------
# KPI definitions  (order = display order in the dashboard)
#
# `filter` describes what is filtered for on a click. It is a partial update
# of the canonical filter state (see data/filtering.py).
# --------------------------------------------------------------------------
KPI_DEFINITIONS = [
    {
        "id": "aktiv",
        "label": "Aktive Materialien",
        "color": KPI_COLORS["green"],
        "value_fn": count_active,
        "filter": {"status": ["Aktiv"], "ohne_klass": False},
    },
    {
        "id": "nicht_geliefert",
        "label": "Nicht gelieferte Teile",
        "color": KPI_COLORS["orange"],
        "value_fn": count_not_delivered,
        "filter": {"status": ["Nicht geliefert"], "ohne_klass": False},
    },
    {
        "id": "obsolet",
        "label": "Obsolete Materialien",
        "color": KPI_COLORS["slate"],
        "value_fn": count_obsolete,
        "filter": {"status": ["Obsolet"], "ohne_klass": False},
    },
    {
        "id": "gesperrt",
        "label": "Gesperrte Materialien",
        "color": KPI_COLORS["red"],
        "value_fn": count_blocked,
        "filter": {"status": ["Gesperrt"], "ohne_klass": False},
    },
    {
        "id": "ohne_klassifizierung",
        "label": "Ohne Klassifizierung",
        "color": KPI_COLORS["purple"],
        "value_fn": count_unclassified,
        # different filter dimension: status does not matter, the "unclassified" flag does
        "filter": {"status": [], "ohne_klass": True},
    },
]


def kpi_filter_map() -> dict[str, dict]:
    """KPI id -> filter update.

    Needed in two places: server-side for the toggle and -- by way of a store --
    client-side for highlighting the tiles. Both fetch the rule from here so that
    there is only ONE source of truth.
    """
    return {k["id"]: k["filter"] for k in KPI_DEFINITIONS}


def compute_kpis(df: pl.DataFrame) -> list[dict]:
    """Computes all KPI values for the given dataset.

    Returns: a list of dicts with id/label/color/value/filter -- usable
    directly in the layout.
    """
    return [
        {
            "id": k["id"],
            "label": k["label"],
            "color": k["color"],
            "value": k["value_fn"](df),
            "filter": k["filter"],
        }
        for k in KPI_DEFINITIONS
    ]
