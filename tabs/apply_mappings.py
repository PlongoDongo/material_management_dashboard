"""
Tab 3 -- "Apply data mappings": provisorischer Platzhalter.
"""
from __future__ import annotations

from dash import html

from config import IDS


def apply_mappings_content() -> html.Div:
    return html.Div(
        id=IDS.CONTENT_MAPPINGS,
        className="tab-content",
        children=[
            html.Div(
                className="placeholder-card",
                children=[
                    html.H2("Apply data mappings", className="placeholder-title"),
                    html.P("Dieser Bereich wird in einem späteren Schritt umgesetzt.",
                           className="placeholder-text"),
                ],
            )
        ],
    )
