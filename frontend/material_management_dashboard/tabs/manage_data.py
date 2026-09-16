"""
Tab 2 -- "Manage data": provisional placeholder.

Scaffolding only; header, footer and the shared sidebars are identical here,
because they live in the top-level layout and are NOT part of the tab content.
"""
from __future__ import annotations

from dash import html

from config import IDS


def manage_data_content() -> html.Div:
    return html.Div(
        id=IDS.CONTENT_MANAGE,
        className="tab-content",
        children=[
            html.Div(
                className="placeholder-card",
                children=[
                    html.H2("Manage data", className="placeholder-title"),
                    html.P("Dieser Bereich wird in einem späteren Schritt umgesetzt.",
                           className="placeholder-text"),
                ],
            )
        ],
    )
