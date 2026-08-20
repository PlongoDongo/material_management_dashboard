"""
Filterlogik auf dem Polars DataFrame.

Der kanonische Filterzustand ist ein einfaches Dict (JSON-serialisierbar,
damit es in einem dcc.Store liegen kann):

    {
        "status":      ["Aktiv", "Gesperrt"],   # leere Liste = kein Constraint
        "werk":        ["Werk Köln"],
        "warengruppe": [],
        "search":      "MAT-101",                # Freitextsuche
        "ohne_klass":  False,                    # nur Materialien ohne Warengruppe
    }

`apply_filters` ist rein (DataFrame + Dict -> DataFrame) und damit gut testbar.
"""
from __future__ import annotations

import polars as pl

# Standard-/Leerzustand des Filters
EMPTY_FILTERS: dict = {
    "status": [],
    "werk": [],
    "warengruppe": [],
    "search": "",
    "ohne_klass": False,
}


def normalize_filters(raw: dict | None) -> dict:
    """Sorgt für vollständige, typsichere Filter (fehlende Keys -> Default)."""
    raw = raw or {}
    return {
        "status": list(raw.get("status") or []),
        "werk": list(raw.get("werk") or []),
        "warengruppe": list(raw.get("warengruppe") or []),
        "search": (raw.get("search") or "").strip(),
        "ohne_klass": bool(raw.get("ohne_klass", False)),
    }


# Spalten, die per Mehrfachauswahl gefiltert werden -- alle nach genau demselben
# Muster (`Spalte is_in gewählte Werte`). Weil sie gleich sind, stehen sie
# datengetrieben hier: eine weitere solche Spalte = ein Eintrag mehr, keine neue
# if-Verzweigung. Die beiden Sonderfälle unten (ohne_klass, search) folgen
# bewusst NICHT diesem Muster und bleiben deshalb ausgeschrieben.
_MULTISELECT_COLUMNS = ("status", "werk", "warengruppe")


def _search_predicate(needle: str) -> pl.Expr:
    """Freitextsuche über Material-Nr. ODER Bezeichnung (case-insensitiv)."""
    needle = needle.lower()
    return (
        pl.col("material_nr").str.to_lowercase().str.contains(needle, literal=True)
        | pl.col("bezeichnung").str.to_lowercase().str.contains(needle, literal=True)
    )


def apply_filters(df: pl.DataFrame, raw_filters: dict | None) -> pl.DataFrame:
    """Wendet den Filterzustand auf den DataFrame an und gibt das Ergebnis zurück.

    Sammelt die aktiven Bedingungen als Liste von Polars-Ausdrücken und wendet sie
    in EINEM `filter`-Aufruf an. Ein leerer Filter (keine aktive Bedingung) gibt
    den DataFrame unverändert zurück.
    """
    f = normalize_filters(raw_filters)

    predicates: list[pl.Expr] = [
        pl.col(col).is_in(f[col]) for col in _MULTISELECT_COLUMNS if f[col]
    ]
    if f["ohne_klass"]:
        predicates.append(
            pl.col("warengruppe").is_null() | (pl.col("warengruppe") == "")
        )
    if f["search"]:
        predicates.append(_search_predicate(f["search"]))

    if not predicates:
        return df

    combined = predicates[0]
    for predicate in predicates[1:]:
        combined = combined & predicate
    return df.filter(combined)
