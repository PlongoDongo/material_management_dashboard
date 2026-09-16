"""
Blue footer tab bar at the bottom edge.

Deliberately NOT a dcc.Tabs component, because we need the mockup's own look
and feel (narrow blue bar at the bottom). The tabs are plain buttons with
stable IDs (= tab ID). The active tab is controlled by the callback in
callbacks/tab_callbacks.py.
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
                        id=tab_id,                      # stable string ID = tab ID
                        n_clicks=0,
                        className="footer-tab" + (" active" if tab_id == active_tab else ""),
                    )
                    for tab_id, label in TABS
                ],
            ),
            html.Div(f"{APP_VERSION} · Mockup", className="footer-version"),
        ],
    )
