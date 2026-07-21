"""
Linke Navigations-Sidebar (initial geschlossen, öffnet über das Menü-Icon).

Inhalt ist hier nur Platzhalter -- die eigentliche Navigation (z. B. zu
weiteren Modulen/Dashboards) kannst du nach Bedarf ergänzen. Für die
aktuellen Anforderungen genügt das reine Gerüst.
"""
from __future__ import annotations

from dash import html

from config import IDS


_NAV_ITEMS = [
    ("▤", "Material Management"),
    ("◷", "Stammdaten-Historie"),
    ("⚙", "Einstellungen"),
]


def nav_sidebar() -> html.Div:
    return html.Div(
        [
            # Halbtransparentes Overlay hinter der Sidebar (Klick = schließen)
            html.Div(id=IDS.NAV_OVERLAY, className="sidebar-overlay", n_clicks=0),
            html.Nav(
                id=IDS.NAV_SIDEBAR,
                className="sidebar sidebar-nav",  # ohne "open" = geschlossen
                children=[
                    html.Div(
                        className="sidebar-header",
                        children=[
                            html.Span("Navigation", className="sidebar-title"),
                            html.Button("×", id=IDS.NAV_CLOSE, n_clicks=0,
                                        className="sidebar-close"),
                        ],
                    ),
                    html.Div(
                        className="sidebar-body",
                        children=[
                            html.Button(
                                [html.Span(icon, className="nav-item-icon"),
                                 html.Span(label)],
                                className="nav-item" + (" active" if i == 0 else ""),
                                n_clicks=0,
                            )
                            for i, (icon, label) in enumerate(_NAV_ITEMS)
                        ],
                    ),
                ],
            ),
        ]
    )
