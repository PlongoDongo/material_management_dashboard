"""
Linke Navigations-Sidebar (initial geschlossen, öffnet über das Menü-Icon).

Anpassen pro Dashboard -- rein in Python
----------------------------------------
Der Inhalt ist eine schlichte Liste `(icon, label)`. Für ein anderes
Dashboard übergibt man einfach eine eigene Liste:

    nav_sidebar(items=[("dashboard", "Übersicht"), ("upload", "Import")])

`icon` ist ein Material-Icons-Name (Ligatur), `label` der Anzeigetext. Das
Styling (Slide-in, Overlay, Farben) steckt zentral in assets/style.css --
darum muss sich niemand im Team kümmern.
"""
from __future__ import annotations

from dash import html

from config import IDS


# (Material-Icons-Name, Beschriftung)
_DEFAULT_NAV_ITEMS = [
    ("inventory_2", "Material Management"),
    ("history", "Stammdaten-Historie"),
    ("settings", "Einstellungen"),
]


def _nav_item(icon: str, label: str, active: bool = False) -> html.Button:
    return html.Button(
        [
            html.I(icon, className="material-icons-outlined nav-item-icon"),
            html.Span(label),
        ],
        className="nav-item" + (" active" if active else ""),
        n_clicks=0,
    )


def nav_sidebar(items: list[tuple[str, str]] | None = None) -> html.Div:
    items = items if items is not None else _DEFAULT_NAV_ITEMS
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
                            html.Button(
                                html.I("close", className="material-icons-outlined"),
                                id=IDS.NAV_CLOSE, n_clicks=0,
                                className="sidebar-close", title="Schließen",
                            ),
                        ],
                    ),
                    html.Div(
                        className="sidebar-body",
                        children=[
                            _nav_item(icon, label, active=(i == 0))
                            for i, (icon, label) in enumerate(items)
                        ],
                    ),
                ],
            ),
        ]
    )
