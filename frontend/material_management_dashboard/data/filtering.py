"""
Filter logic on the Polars DataFrame.

The canonical filter state is a plain dict (JSON-serialisable, so that it can
live in a dcc.Store):

    {
        "status":      ["Aktiv", "Gesperrt"],   # empty list = no constraint
        "plant":        ["Werk Köln"],
        "material_group": [],
        "search":      "MAT-101",                # free-text search
        "ohne_klass":  False,                    # only materials without a group
    }

`apply_filters` is pure (DataFrame + dict -> DataFrame) and therefore easy to
test.
"""
from __future__ import annotations

import polars as pl

# Default / empty state of the filter
EMPTY_FILTERS: dict = {
    "status": [],
    "plant": [],
    "material_group": [],
    "search": "",
    "ohne_klass": False,
}


def normalize_filters(raw: dict | None) -> dict:
    """Produces complete, type-safe filters (missing keys -> default)."""
    raw = raw or {}
    return {
        "status": list(raw.get("status") or []),
        "plant": list(raw.get("plant") or []),
        "material_group": list(raw.get("material_group") or []),
        "search": (raw.get("search") or "").strip(),
        "ohne_klass": bool(raw.get("ohne_klass", False)),
    }


# Columns that are filtered by multi-select -- all following exactly the same
# pattern (`column is_in selected values`). Because they are alike, they sit
# here data-driven: another such column = one more entry, not a new if branch.
# The two special cases below (ohne_klass, search) deliberately do NOT follow
# this pattern and are therefore spelled out.
_MULTISELECT_COLUMNS = ("status", "plant", "material_group")


def _search_predicate(needle: str) -> pl.Expr:
    """Free-text search over material number OR description (case-insensitive)."""
    needle = needle.lower()
    return (
        pl.col("material_number").str.to_lowercase().str.contains(needle, literal=True)
        | pl.col("description").str.to_lowercase().str.contains(needle, literal=True)
    )


def apply_filters(df: pl.DataFrame, raw_filters: dict | None) -> pl.DataFrame:
    """Applies the filter state to the DataFrame and returns the result.

    Collects the active conditions as a list of Polars expressions and applies
    them in ONE `filter` call. An empty filter (no active condition) returns the
    DataFrame unchanged.
    """
    f = normalize_filters(raw_filters)

    predicates: list[pl.Expr] = [
        pl.col(col).is_in(f[col]) for col in _MULTISELECT_COLUMNS if f[col]
    ]
    if f["ohne_klass"]:
        predicates.append(
            pl.col("material_group").is_null() | (pl.col("material_group") == "")
        )
    if f["search"]:
        predicates.append(_search_predicate(f["search"]))

    if not predicates:
        return df

    combined = predicates[0]
    for predicate in predicates[1:]:
        combined = combined & predicate
    return df.filter(combined)
