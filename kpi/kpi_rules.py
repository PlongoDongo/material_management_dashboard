"""
Regelbasierte KPI-Berechnung.

Jede KPI hat:
  * eine reine Funktion `value_fn(df) -> int`, die den Wert aus dem
    VOLLSTÄNDIGEN Datensatz berechnet (die KPIs zeigen die Gesamtlage,
    unabhängig vom aktuellen Tabellenfilter),
  * einen `filter`-Dict, der beim Klick auf die Kachel angewendet wird.

Die Funktionen sind bewusst pur (nur Polars rein/Zahl raus), damit sie
in PyTest ohne Dash/DB testbar sind.
"""
from __future__ import annotations

import polars as pl

from config import KPI_COLORS


# --------------------------------------------------------------------------
# Reine Berechnungsregeln  (df ist ein Polars DataFrame)
# --------------------------------------------------------------------------
def _count_status(df: pl.DataFrame, status: str) -> int:
    return int(df.filter(pl.col("status") == status).height)


def count_aktiv(df: pl.DataFrame) -> int:
    return _count_status(df, "Aktiv")


def count_nicht_geliefert(df: pl.DataFrame) -> int:
    return _count_status(df, "Nicht geliefert")


def count_obsolet(df: pl.DataFrame) -> int:
    return _count_status(df, "Obsolet")


def count_gesperrt(df: pl.DataFrame) -> int:
    return _count_status(df, "Gesperrt")


def count_ohne_klassifizierung(df: pl.DataFrame) -> int:
    """Materialien ohne Warengruppe (null oder leer)."""
    return int(
        df.filter(
            pl.col("warengruppe").is_null() | (pl.col("warengruppe") == "")
        ).height
    )


# --------------------------------------------------------------------------
# KPI-Definitionen  (Reihenfolge = Anzeige-Reihenfolge im Dashboard)
#
# `filter` beschreibt, wonach beim Klick gefiltert wird. Es ist ein
# Teil-Update des kanonischen Filterzustands (siehe data/filtering.py).
# --------------------------------------------------------------------------
KPI_DEFINITIONS = [
    {
        "id": "aktiv",
        "label": "Aktive Materialien",
        "color": KPI_COLORS["green"],
        "value_fn": count_aktiv,
        "filter": {"status": ["Aktiv"], "ohne_klass": False},
    },
    {
        "id": "nicht_geliefert",
        "label": "Nicht gelieferte Teile",
        "color": KPI_COLORS["orange"],
        "value_fn": count_nicht_geliefert,
        "filter": {"status": ["Nicht geliefert"], "ohne_klass": False},
    },
    {
        "id": "obsolet",
        "label": "Obsolete Materialien",
        "color": KPI_COLORS["slate"],
        "value_fn": count_obsolet,
        "filter": {"status": ["Obsolet"], "ohne_klass": False},
    },
    {
        "id": "gesperrt",
        "label": "Gesperrte Materialien",
        "color": KPI_COLORS["red"],
        "value_fn": count_gesperrt,
        "filter": {"status": ["Gesperrt"], "ohne_klass": False},
    },
    {
        "id": "ohne_klassifizierung",
        "label": "Ohne Klassifizierung",
        "color": KPI_COLORS["purple"],
        "value_fn": count_ohne_klassifizierung,
        # andere Filterdimension: Status egal, dafür Flag "ohne Klassifizierung"
        "filter": {"status": [], "ohne_klass": True},
    },
]


def compute_kpis(df: pl.DataFrame) -> list[dict]:
    """Berechnet alle KPI-Werte für den übergebenen Datensatz.

    Rückgabe: Liste von Dicts mit id/label/color/value/filter -- direkt
    im Layout verwendbar.
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
