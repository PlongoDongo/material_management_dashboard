"""
Column schema of the material table -- the ONE truth.

Columns used to be defined in three places (the COLUMNS list + COLUMN_LABELS in
repository.py, _COL_MIN_WIDTH in data_overview.py). Adding or removing a column
meant keeping three places in sync.

Now `MATERIAL_COLUMNS` describes every column ONCE (id, label, width, type,
pinning); everything else is derived from it. Changing a column = changing one
line.

Deliberately free of Dash and HTTP. The column ids are the field names of the
data product, so `data/repository.py` needs no mapping: it keeps the columns
listed here and drops the rest. The labels are German because they are what the
user reads.

History: until the move to the API layer there was an `einheit` column here.
The `material-overview` data product no longer delivers it as of v2, offering
the computed `stock_value` instead. That is exactly what versioning the data
products is for -- v1 still delivers `einheit` should anyone need it after all.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    """Definition of exactly one table column."""
    id: str
    label: str
    min_width: int
    numeric: bool = False      # -> DataTable format + right-aligned
    fixed: bool = False        # pinned left / always visible (cannot be deselected)
    # Room for later extensions (see review), e.g.:
    # filterable: bool = True
    # filter_kind: str = "text"


# Order = display order in the table. The pinned columns deliberately come
# first so that they can be frozen on the left (fixed_columns).
MATERIAL_COLUMNS: list[Column] = [
    Column("material_number", "Material-Nr.", 130, fixed=True),
    Column("description", "Bezeichnung", 220, fixed=True),
    Column("material_group", "Warengruppe", 160),
    Column("plant_name",   "Werk",        140),
    Column("status",      "Status",      150),
    Column("stock",     "Bestand",     110, numeric=True),
    Column("stock_value", "Bestandswert", 130, numeric=True),
    Column("changed_on",   "Geändert",    120),
]

# --- Derived (do not maintain by hand) ------------------------------------
COLUMNS: list[str] = [c.id for c in MATERIAL_COLUMNS]
COLUMN_LABELS: dict[str, str] = {c.id: c.label for c in MATERIAL_COLUMNS}
COL_MIN_WIDTH: dict[str, int] = {c.id: c.min_width for c in MATERIAL_COLUMNS}
FIXED_COLUMNS: tuple[str, ...] = tuple(c.id for c in MATERIAL_COLUMNS if c.fixed)
NUMERIC_COLUMNS: tuple[str, ...] = tuple(c.id for c in MATERIAL_COLUMNS if c.numeric)
