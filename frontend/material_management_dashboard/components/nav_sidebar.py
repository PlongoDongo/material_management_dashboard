"""
Left-hand navigation sidebar (initially closed, opens via the menu icon).

Adjusting it per dashboard -- purely in Python
----------------------------------------------
The content is a plain list of `(icon, label)`. For a different dashboard you
simply pass in your own list:

    nav_sidebar(items=[("dashboard", "Übersicht"), ("upload", "Import")])

`icon` is a Material Icons name (ligature), `label` the displayed text. The
styling (slide-in, overlay, colors) sits centrally in assets/style.css --
so nobody on the team has to worry about it.
"""
from __future__ import annotations

from dash import html

from config import IDS


# Per entry: Material Icons name plus label
_DEFAULT_NAV_ITEMS = [
    ("inventory_2", "Material Management"),
    ("history", "Stammdaten-Historie"),
    ("settings", "Einstellungen"),
]


def _nav_item(icon: str, label: str, *, active: bool = False) -> html.Button:
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
            # Semi-transparent overlay behind the sidebar (click = close)
            html.Div(id=IDS.NAV_OVERLAY, className="sidebar-overlay", n_clicks=0),
            html.Nav(
                id=IDS.NAV_SIDEBAR,
                className="sidebar sidebar-nav",  # without "open" = closed
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
