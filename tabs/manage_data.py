"""
Tab 2 -- "Manage data": provisorischer Platzhalter.

Nur Gerüst; Header, Footer und die geteilten Sidebars sind identisch, weil sie
im Top-Level-Layout liegen und NICHT Teil des Tab-Inhalts sind.
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
