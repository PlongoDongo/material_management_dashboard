"""
Wiederverwendbare Header-Zeile (Unternehmensblau).

Aufbau (wie im Mockup):
  links   : Menü-Icon (Hamburger)  -> öffnet die linke Nav-Sidebar
  mitte   : Titel + Untertitel + Firmenlogo-Chip
  rechts  : Filter-Icon             -> öffnet die rechte Filter-Sidebar

Diese Datei ist bewusst parametrisiert (title/subtitle), damit sie -- wie
bei deinem vorherigen Dashboard -- 1:1 wiederverwendet werden kann. Die
IDs (MENU_BTN, FILTER_BTN) sind stabil; die zugehörigen Callbacks liegen in
`header_callbacks.py`. Wenn du bereits eine header_layout.py hast, gleiche
einfach die IDs ab, dann greifen die Callbacks unverändert.
"""
from __future__ import annotations

from dash import html

from config import IDS, APP_TITLE, APP_SUBTITLE


def _icon_button(btn_id: str, glyph: str, title: str, side: str) -> html.Button:
    return html.Button(
        glyph,
        id=btn_id,
        title=title,
        n_clicks=0,
        className=f"header-icon-btn header-icon-{side}",
    )


def header_layout(title: str = APP_TITLE, subtitle: str = APP_SUBTITLE) -> html.Header:
    return html.Header(
        className="app-header",
        children=[
            # Linke Seite: Menü-Icon + Titelblock
            html.Div(
                className="header-left",
                children=[
                    _icon_button(IDS.MENU_BTN, "☰", "Navigationsmenü", "left"),  # ☰
                    html.Div(
                        className="header-titles",
                        children=[
                            html.Span(title, className="header-title"),
                            html.Span("|", className="header-divider"),
                            html.Span(subtitle, className="header-subtitle"),
                        ],
                    ),
                ],
            ),
            # Rechte Seite: Logo-Chip + Filter-Icon
            html.Div(
                className="header-right",
                children=[
                    html.Div(
                        className="header-logo-chip",
                        children=[
                            html.Span("◆", className="logo-diamond"),  # ◆
                            html.Span("ACME", className="logo-name"),
                            html.Span("Industries", className="logo-sub"),
                        ],
                    ),
                    _icon_button(IDS.FILTER_BTN, "≡", "Filter", "right"),  # ≡ (Filter)
                ],
            ),
        ],
    )
