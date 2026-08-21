"""
Tab 1 -- "Data overview": KPI-Kacheln + Materialtabelle.

Layout-Prinzip
--------------
Der gesamte Tab-Inhalt liegt PERMANENT im DOM; die Sichtbarkeit wird nur per
CSS umgeschaltet (siehe app.py / tab_callbacks). Dadurch bleibt die Tabelle
immer als gültiges Callback-Ziel bestehen, und der Filterzustand (im Store)
wird beim Zurückwechseln sofort wieder angewandt -- ohne Timing-Probleme mit
dynamisch erzeugten Komponenten.

KPI-Werte werden im Backend über `compute_kpis()` (kpi/kpi_rules.py) berechnet.
Ein Klick auf eine Kachel setzt den zugehörigen Filter (callbacks/).
"""
from __future__ import annotations

from dash import dash_table, dcc, html
from dash.dash_table.Format import Format, Group

from config import IDS, STATUS_COLORS
from data.schema import (
    COL_MIN_WIDTH,
    COLUMN_LABELS,
    COLUMNS,
    FIXED_COLUMNS,
    MATERIAL_COLUMNS,
    NUMERIC_COLUMNS,
)
from data.repository import get_materials
from kpi.kpi_rules import compute_kpis

# Spalten, die der User über das Popover an-/abwählen kann (alle außer den
# fixierten Material-Nr. / Bezeichnung).
_TOGGLEABLE_COLUMNS = [c for c in COLUMNS if c not in FIXED_COLUMNS]


# --------------------------------------------------------------------------
# KPI-Kacheln
# --------------------------------------------------------------------------
def _kpi_tile(kpi: dict) -> html.Button:
    """Eine farbige, anklickbare KPI-Kachel."""
    return html.Button(
        id={"type": "kpi-tile", "kpi": kpi["id"]},
        n_clicks=0,
        className="kpi-tile",
        style={"backgroundColor": kpi["color"]},
        children=[
            html.Div(f"{kpi['value']:,}".replace(",", "."), className="kpi-value"),
            html.Div(kpi["label"], className="kpi-label"),
        ],
    )


def kpi_row() -> html.Div:
    kpis = compute_kpis(get_materials())
    return html.Div(
        id=IDS.KPI_ROW,
        className="kpi-row",
        children=[_kpi_tile(k) for k in kpis],
    )


# --------------------------------------------------------------------------
# Materialtabelle
# --------------------------------------------------------------------------
def _table_columns() -> list[dict]:
    """DataTable-Spalten aus dem zentralen Schema (data/schema.py)."""
    cols = []
    for c in MATERIAL_COLUMNS:
        col = {"name": c.label.upper(), "id": c.id}
        if c.numeric:
            col.update(
                type="numeric",
                format=Format(group=Group.yes, groups=3, group_delimiter="."),
            )
        cols.append(col)
    return cols


def _column_width_conditional() -> list[dict]:
    """Mindestbreite je Spalte (aus dem Schema) -> erzwingt bei Bedarf den
    Scrollbalken. Numerische Spalten rechtsbündig."""
    styles = [
        {
            "if": {"column_id": c},
            "minWidth": f"{COL_MIN_WIDTH[c]}px",
            "width": f"{COL_MIN_WIDTH[c]}px",
        }
        for c in COLUMNS
    ]
    styles += [{"if": {"column_id": c}, "textAlign": "right"}
               for c in NUMERIC_COLUMNS]
    return styles


def _status_style_conditional() -> list[dict]:
    """Färbt den Status-Text passend zur Statusfarbe (Punkt-Ersatz)."""
    return [
        {
            "if": {"filter_query": f'{{status}} = "{status}"', "column_id": "status"},
            "color": color,
            "fontWeight": "600",
        }
        for status, color in STATUS_COLORS.items()
    ]


def material_table() -> dash_table.DataTable:
    return dash_table.DataTable(
        id=IDS.TABLE,
        columns=_table_columns(),
        data=[],  # wird per Callback aus dem gefilterten Polars-DF gefüllt
        page_size=20,
        sort_action="native",
        # Automatische Filterzeile unter den Spaltenköpfen (neben den Sortier-
        # pfeilen). Native = Textfilter mit Operatoren (=, >, contains, ...),
        # case-insensitiv. Ergänzt die globalen Sidebar-Filter; siehe
        # Erläuterung im Chat zur Werte-Auswahl.
        filter_action="native",
        filter_options={"case": "insensitive", "placeholder_text": "filtern …"},
        # Sichtbarkeit der Spalten steuert das Spalten-Popover (Callback ->
        # hidden_columns). Initial sind alle sichtbar.
        hidden_columns=[],
        # Kopf- + Filterzeile bleiben beim vertikalen Scrollen oben stehen; der
        # Tabellenkörper scrollt INNERHALB der Tabelle (Höhe kommt aus dem
        # Flex-Layout der Karte, siehe style.css / .table-card).
        #
        # Bewusst KEIN fixed_columns (Einfrieren der linken Spalten beim
        # horizontalen Scrollen): dessen geteilte Render-Struktur zerlegt in
        # zwei häufigen Zuständen die Kopfzeile -- bei leerer Tabelle
        # (Filter ohne Treffer) verschwinden ALLE Spaltennamen, und wenn nur die
        # zwei nicht-abwählbaren Spalten übrig sind, fehlt der zweiten der
        # Header. Ohne fixed_columns rendern beide Fälle korrekt. Material-Nr.
        # und Bezeichnung bleiben trotzdem "immer sichtbar", weil sie im
        # Spalten-Popover nicht abwählbar sind -- nur das Einfrieren beim
        # Horizontal-Scrollen entfällt (greift ohnehin nur bei schmalen
        # Fenstern; siehe Chat für die Abwägung/Alternative).
        fixed_rows={"headers": True},
        style_as_list_view=True,
        # overflow auto in beide Richtungen: vertikal scrollt der Körper (dank
        # fixed_rows), horizontal die zu breiten Spalten -- beides INNERHALB der
        # Tabelle. Die HÖHE gibt bewusst das Flex-Layout der Karte vor (siehe
        # style.css) und NICHT height:100% -- sonst verdrängte die Tabelle die
        # darunterliegende Pagination aus der Karte.
        style_table={"overflowY": "auto", "overflowX": "auto",
                     "width": "100%", "minWidth": "100%"},
        style_filter={"backgroundColor": "#fbfcfd"},
        style_header={
            "backgroundColor": "#f4f7fa",
            "fontWeight": "700",
            "fontSize": "11px",
            "letterSpacing": "0.4px",
            "color": "#5b6b7d",
            "border": "none",
            "borderBottom": "1px solid #dbe2ea",
        },
        style_cell={
            "fontFamily": "system-ui, sans-serif",
            "fontSize": "13px",
            "padding": "10px 14px",
            "border": "none",
            "borderBottom": "1px solid #eef1f4",
            "color": "#1b2733",
            "textAlign": "left",
            # Kein Umbruch -> Spalten behalten ihre Breite, statt sich beim
            # Schrumpfen mehrzeilig zu stapeln.
            "whiteSpace": "nowrap",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
        },
        style_cell_conditional=_column_width_conditional(),
        style_data_conditional=_status_style_conditional(),
        # Sobald hidden_columns gesetzt ist, blendet die DataTable von sich aus
        # ihr eigenes "Toggle Columns"-Menü ein (.dash-spreadsheet-menu). Das
        # Ein-/Ausblenden übernimmt bereits unser Spalten-Popover, darum das
        # eingebaute Menü ausblenden. `css` wird von der Komponente selbst
        # injiziert -> robuster als eine globale Regel.
        css=[{"selector": ".dash-spreadsheet-menu", "rule": "display: none;"}],
    )


# --------------------------------------------------------------------------
# Spaltenauswahl-Popover (gehört visuell zur Tabelle)
# --------------------------------------------------------------------------
def _column_menu() -> html.Div:
    """Button + aufklappbares Panel zum Ein-/Ausblenden von Spalten.

    Öffnen/Schließen und das Ableiten von hidden_columns laufen clientseitig
    (assets/column_menu.js, callbacks/column_callbacks.py) -- kein Server-
    Roundtrip, damit es sich unmittelbar anfühlt.
    """
    fixed_labels = " und ".join(COLUMN_LABELS[c] for c in FIXED_COLUMNS)
    return html.Div(
        className="col-menu",
        children=[
            html.Button(
                [
                    html.I("view_column", className="material-icons-outlined"),
                    html.Span("Spalten"),
                ],
                id=IDS.COLS_BTN, n_clicks=0, className="inline-filter-btn",
                title="Spalten ein-/ausblenden",
            ),
            html.Div(
                id=IDS.COLS_MENU,
                className="col-menu-panel",  # ohne "open" = zu
                children=[
                    html.Div(
                        className="col-menu-head",
                        children=[
                            html.Span("Spalten anzeigen", className="col-menu-title"),
                            html.Div(
                                className="col-menu-actions",
                                children=[
                                    html.Button("Alle", id=IDS.COLS_ALL,
                                                n_clicks=0, className="col-menu-link"),
                                    html.Button("Keine", id=IDS.COLS_NONE,
                                                n_clicks=0, className="col-menu-link"),
                                ],
                            ),
                        ],
                    ),
                    dcc.Checklist(
                        id=IDS.COLS_CHECKLIST,
                        options=[{"label": COLUMN_LABELS[c], "value": c}
                                 for c in _TOGGLEABLE_COLUMNS],
                        value=list(_TOGGLEABLE_COLUMNS),  # initial alle sichtbar
                        className="col-menu-list",
                        persistence=True, persistence_type="session",
                    ),
                    html.Div(
                        [
                            html.I("push_pin", className="material-icons-outlined"),
                            html.Span(f"{fixed_labels} bleiben immer sichtbar."),
                        ],
                        className="col-menu-note",
                    ),
                ],
            ),
        ],
    )


# --------------------------------------------------------------------------
# Gesamter Tab-Inhalt
# --------------------------------------------------------------------------
def data_overview_content() -> html.Div:
    return html.Div(
        id=IDS.CONTENT_OVERVIEW,
        className="tab-content",
        children=[
            kpi_row(),
            html.Div(
                className="table-card",
                children=[
                    html.Div(
                        className="table-toolbar",
                        children=[
                            html.Div(
                                className="table-heading",
                                children=[
                                    html.Span("Materialübersicht",
                                              className="table-title"),
                                    html.Span(id=IDS.RECORD_COUNTER,
                                              className="record-counter"),
                                ],
                            ),
                            _column_menu(),
                        ],
                    ),
                    material_table(),
                ],
            ),
        ],
    )
