"""
Blaue Footer-Tab-Leiste am unteren Rand.

Bewusst KEINE dcc.Tabs-Komponente, weil wir das eigene Look-and-Feel des
Mockups (schmale blaue Leiste unten) brauchen. Die Tabs sind einfache Buttons
mit stabilen IDs (= Tab-ID). Der aktive Tab wird über den Callback in
callbacks/tab_callbacks.py gesteuert.
"""
from __future__ import annotations

from dash import html

from config import IDS, TABS, APP_VERSION


def footer_tabs(active_tab: str = IDS.TAB_OVERVIEW) -> html.Footer:
    return html.Footer(
        className="app-footer",
        children=[
            html.Div(
                className="footer-tabs",
                children=[
                    html.Button(
                        [html.Span(className="tab-dot"), label],
                        id=tab_id,                      # stabile String-ID = Tab-ID
                        n_clicks=0,
                        className="footer-tab" + (" active" if tab_id == active_tab else ""),
                    )
                    for tab_id, label in TABS
                ],
            ),
            html.Div(f"{APP_VERSION} · Mockup", className="footer-version"),
        ],
    )
