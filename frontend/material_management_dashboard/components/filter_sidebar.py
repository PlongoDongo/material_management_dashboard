"""
Right-hand filter sidebar (initially closed, opens via the filter icon or the
"Filter" button next to the table).

IMPORTANT for the persistence requirement:
This sidebar lives in the TOP-LEVEL layout (app.layout), NOT inside the tab
content. That keeps it permanently in the DOM when switching tabs -> its
values are never lost. On top of that, `persistence=True,
persistence_type="session"` makes the selection survive even a browser reload.

The controls are the filter's "source of truth". A callback mirrors them into
the canonical `store-filters` (see callbacks/).
"""
from __future__ import annotations

from dash import dcc, html
from dash.development.base_component import Component

from config import IDS
from data.repository import distinct_values

_STATUS_OPTIONS = ["Aktiv", "Nicht geliefert", "Obsolet", "Gesperrt"]


def _multi_dropdown(cid: str, placeholder: str, options: list[str]) -> dcc.Dropdown:
    return dcc.Dropdown(
        id=cid,
        options=[{"label": o, "value": o} for o in options],
        value=[],
        multi=True,
        placeholder=placeholder,
        persistence=True,
        persistence_type="session",
        className="filter-dropdown",
    )


def _field(label: str, control: Component) -> html.Div:
    return html.Div(
        className="filter-field",
        children=[html.Label(label, className="filter-label"), control],
    )


def filter_sidebar() -> html.Div:
    return html.Div(
        [
            html.Div(id=IDS.FILTER_OVERLAY, className="sidebar-overlay", n_clicks=0),
            html.Aside(
                id=IDS.FILTER_SIDEBAR,
                className="sidebar sidebar-filter",  # without "open" = closed
                children=[
                    html.Div(
                        className="sidebar-header",
                        children=[
                            html.Span("Filter", className="sidebar-title"),
                            html.Button(
                                html.I("close", className="material-icons-outlined"),
                                id=IDS.FILTER_CLOSE, n_clicks=0,
                                className="sidebar-close", title="Schließen"),
                        ],
                    ),
                    html.Div(
                        className="sidebar-body",
                        children=[
                            _field("Suche (Material-Nr. / Bezeichnung)",
                                   dcc.Input(
                                       id=IDS.F_SEARCH, type="text", value="",
                                       placeholder="z. B. MAT-101 oder Dichtungsring",
                                       debounce=True,
                                       persistence=True, persistence_type="session",
                                       className="filter-input")),
                            _field("Status",
                                   _multi_dropdown(IDS.F_STATUS, "Alle Status",
                                                   _STATUS_OPTIONS)),
                            _field("Werk",
                                   _multi_dropdown(IDS.F_PLANT, "Alle Werke",
                                                   distinct_values("plant"))),
                            _field("Warengruppe",
                                   _multi_dropdown(IDS.F_MATERIAL_GROUP, "Alle Warengruppen",
                                                   distinct_values("material_group"))),
                            dcc.Checklist(
                                id=IDS.F_OHNE_KLASS,
                                options=[{"label": " Nur ohne Klassifizierung",
                                          "value": "on"}],
                                value=[],
                                persistence=True, persistence_type="session",
                                className="filter-check",
                            ),
                            html.Button("Filter zurücksetzen", id=IDS.F_RESET,
                                        n_clicks=0, className="filter-reset-btn"),
                        ],
                    ),
                ],
            ),
        ]
    )
