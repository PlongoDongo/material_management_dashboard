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


def apply_filters(df: pl.DataFrame, raw_filters: dict | None) -> pl.DataFrame:
    """Wendet den Filterzustand auf den DataFrame an und gibt das Ergebnis zurück."""
    f = normalize_filters(raw_filters)
    out = df

    if f["status"]:
        out = out.filter(pl.col("status").is_in(f["status"]))

    if f["werk"]:
        out = out.filter(pl.col("werk").is_in(f["werk"]))

    if f["warengruppe"]:
        out = out.filter(pl.col("warengruppe").is_in(f["warengruppe"]))

    if f["ohne_klass"]:
        out = out.filter(
            pl.col("warengruppe").is_null() | (pl.col("warengruppe") == "")
        )

    if f["search"]:
        needle = f["search"].lower()
        out = out.filter(
            pl.col("material_nr").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("bezeichnung").str.to_lowercase().str.contains(needle, literal=True)
        )

    return out
