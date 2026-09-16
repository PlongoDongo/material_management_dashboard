"""
Tab 1 -- "Data overview": KPI tiles + material table.

Layout principle
----------------
The entire tab content stays PERMANENTLY in the DOM; only its visibility is
toggled via CSS (see app.py / tab_callbacks). That keeps the table around as a
valid callback target at all times, and the filter state (in the store) is
re-applied immediately when switching back -- without timing problems caused
by dynamically created components.

KPI values are computed on the backend via `compute_kpis()` (kpi/kpi_rules.py).
Clicking a tile sets the corresponding filter (callbacks/).
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

# Columns the user can show/hide via the popover (all except the pinned
# material number / description).
_TOGGLEABLE_COLUMNS = [c for c in COLUMNS if c not in FIXED_COLUMNS]


# --------------------------------------------------------------------------
# KPI tiles
# --------------------------------------------------------------------------
def _kpi_tile(kpi: dict) -> html.Button:
    """A colored, clickable KPI tile."""
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
# Material table
# --------------------------------------------------------------------------
def _table_columns() -> list[dict]:
    """DataTable columns derived from the central schema (data/schema.py)."""
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
    """Minimum width per column (from the schema) -> forces the scrollbar when
    needed. Numeric columns are right-aligned."""
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
    """Colors the status text to match the status color (dot replacement)."""
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
        data=[],  # filled by a callback from the filtered Polars DF
        page_size=20,
        sort_action="native",
        # Automatic filter row below the column headers (next to the sort
        # arrows). Native = text filter with operators (=, >, contains, ...),
        # case-insensitive. Complements the global sidebar filters; see the
        # explanation in the chat about picking values.
        filter_action="native",
        filter_options={"case": "insensitive", "placeholder_text": "filtern …"},
        # Column visibility is driven by the column popover (callback ->
        # hidden_columns). Initially all of them are visible.
        hidden_columns=[],
        # Header + filter row stay pinned at the top while scrolling
        # vertically; the table body scrolls INSIDE the table (the height comes
        # from the card's flex layout, see style.css / .table-card).
        #
        # Deliberately NO fixed_columns (freezing the left-hand columns while
        # scrolling horizontally): its split render structure breaks the header
        # row in two common states -- with an empty table (filter without
        # matches) ALL column names disappear, and when only the two
        # non-deselectable columns are left, the second one is missing its
        # header. Without fixed_columns both cases render correctly. Material
        # number and description still stay "always visible", because they
        # cannot be deselected in the column popover -- only the freezing while
        # scrolling horizontally is gone (which only matters for narrow windows
        # anyway; see the chat for the trade-off/alternative).
        fixed_rows={"headers": True},
        style_as_list_view=True,
        # overflow auto in both directions: vertically the body scrolls (thanks
        # to fixed_rows), horizontally the overly wide columns -- both INSIDE
        # the table. The HEIGHT is deliberately dictated by the card's flex
        # layout (see style.css) and NOT by height:100% -- otherwise the table
        # would push the pagination below it out of the card.
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
            # No wrapping -> columns keep their width instead of stacking onto
            # several lines when they shrink.
            "whiteSpace": "nowrap",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
        },
        style_cell_conditional=_column_width_conditional(),
        style_data_conditional=_status_style_conditional(),
        # As soon as hidden_columns is set, the DataTable shows its own "Toggle
        # Columns" menu (.dash-spreadsheet-menu) by itself. Showing/hiding is
        # already handled by our column popover, so the built-in menu gets
        # hidden. `css` is injected by the component itself -> more robust than
        # a global rule.
        css=[{"selector": ".dash-spreadsheet-menu", "rule": "display: none;"}],
    )


# --------------------------------------------------------------------------
# Column selection popover (belongs visually to the table)
# --------------------------------------------------------------------------
def _column_menu() -> html.Div:
    """Button + expandable panel for showing/hiding columns.

    Opening/closing and deriving hidden_columns happen on the client side
    (assets/column_menu.js, callbacks/column_callbacks.py) -- no server round
    trip, so that it feels immediate.
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
                className="col-menu-panel",  # without "open" = closed
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
                        value=list(_TOGGLEABLE_COLUMNS),  # all visible initially
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
# Complete tab content
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
