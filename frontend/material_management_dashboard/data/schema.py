"""
Spaltenschema der Materialtabelle -- die EINE Wahrheit.

Früher waren Spalten an drei Stellen definiert (COLUMNS-Liste + COLUMN_LABELS
in repository.py, _COL_MIN_WIDTH in data_overview.py). Eine Spalte hinzufügen
oder entfernen hieß, drei Stellen synchron zu halten.

Jetzt beschreibt `MATERIAL_COLUMNS` jede Spalte EINMAL (id, Label, Breite, Typ,
Fixierung); alles Weitere wird daraus abgeleitet. Eine Spalte ändern = eine
Zeile ändern.

Bewusst frei von Dash und HTTP: Das Schema beschreibt, was die TABELLE zeigt --
nicht, was die API liefert. Beides wird in `data/repository.py::_API_TO_UI`
aufeinander abgebildet.

Historie: Bis zum Umstieg auf den API-Layer gab es hier eine Spalte `einheit`.
Das Datenprodukt `material-overview` liefert sie ab v2 nicht mehr, dafür den
berechneten `bestandswert`. Genau dafür ist die Versionierung der Datenprodukte
da -- v1 liefert weiterhin `einheit`, falls jemand sie doch braucht.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    """Definition genau einer Tabellenspalte."""
    id: str
    label: str
    min_width: int
    numeric: bool = False      # -> DataTable-Format + rechtsbündig
    fixed: bool = False        # links fixiert / immer sichtbar (nicht abwählbar)
    # Platz für spätere Erweiterungen (siehe Review), z. B.:
    # filterable: bool = True
    # filter_kind: str = "text"


# Reihenfolge = Anzeigereihenfolge in der Tabelle. Die fixierten Spalten stehen
# bewusst vorn, damit sie sich links einfrieren lassen (fixed_columns).
MATERIAL_COLUMNS: list[Column] = [
    Column("material_nr", "Material-Nr.", 130, fixed=True),
    Column("bezeichnung", "Bezeichnung", 220, fixed=True),
    Column("warengruppe", "Warengruppe", 160),
    Column("werk",        "Werk",        140),
    Column("status",      "Status",      150),
    Column("bestand",     "Bestand",     110, numeric=True),
    Column("bestandswert", "Bestandswert", 130, numeric=True),
    Column("geaendert",   "Geändert",    120),
]

# --- Abgeleitetes (nicht von Hand pflegen) --------------------------------
COLUMNS: list[str] = [c.id for c in MATERIAL_COLUMNS]
COLUMN_LABELS: dict[str, str] = {c.id: c.label for c in MATERIAL_COLUMNS}
COL_MIN_WIDTH: dict[str, int] = {c.id: c.min_width for c in MATERIAL_COLUMNS}
FIXED_COLUMNS: tuple[str, ...] = tuple(c.id for c in MATERIAL_COLUMNS if c.fixed)
NUMERIC_COLUMNS: tuple[str, ...] = tuple(c.id for c in MATERIAL_COLUMNS if c.numeric)
