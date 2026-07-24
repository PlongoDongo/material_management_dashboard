"""
Rechte Filter-Sidebar (initial geschlossen, öffnet über das Filter-Icon
oder den "Filter"-Button neben der Tabelle).

WICHTIG für die Persistenz-Anforderung:
Diese Sidebar liegt im TOP-LEVEL-Layout (app.layout), NICHT im Tab-Inhalt.
Dadurch bleibt sie beim Tab-Wechsel dauerhaft im DOM -> ihre Werte gehen nie
verloren. Zusätzlich sorgt `persistence=True, persistence_type="session"`
dafür, dass die Auswahl sogar einen Browser-Reload übersteht.

Die Steuerelemente sind die "Wahrheitsquelle" des Filters. Ein Callback
spiegelt sie in den kanonischen `store-filters` (siehe callbacks/).
"""
from __future__ import annotations

from dash import dcc, html

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


def _field(label: str, control) -> html.Div:
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
                className="sidebar sidebar-filter",  # ohne "open" = geschlossen
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
                                   _multi_dropdown(IDS.F_WERK, "Alle Werke",
                                                   distinct_values("werk"))),
                            _field("Warengruppe",
                                   _multi_dropdown(IDS.F_WARENGRUPPE, "Alle Warengruppen",
                                                   distinct_values("warengruppe"))),
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
