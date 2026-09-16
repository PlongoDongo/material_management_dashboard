"""
Reusable header (team standard).

Structure (same order as before):

    ┌─ Restriction mini bar ("Restricted") ─────────────────────────────────┐
    ├───────────────────────────────────────────────────────────────────────┤
    │ [☰ Menu] │ Logo │ Title/Subtitle         …Filler…        │ [⛃ Filter] │
    └───────────────────────────────────────────────────────────────────────┘

Reuse in other dashboards
-------------------------
`header_layout()` is deliberately parameterized -- title, subtitle, logo and
restriction text come in as arguments, and the button IDs are configurable.
A new dashboard simply calls `header_layout(title=..., subtitle=...)`; the
matching open/close callbacks live in `callbacks/header_callbacks.py`.

The header deliberately contains NO sidebars any more. The nav and filter
sidebars are components of their own (components/nav_sidebar.py,
filter_sidebar.py) and live in the top-level layout. That keeps the header
lean and identical in every dashboard, while each app hangs its own sidebars
next to it.

Icons
-----
Material Icons are loaded via a stylesheet (linked in app.py as an
external_stylesheet) and referenced by ligature name -- so
`html.I("menu", className="material-icons-outlined")` instead of a special
character inside the string. That way the rendering does not depend on a
copied glyph and stays consistent.

Styling
-------
The classes used here (`team-header`, `main-header`, `panel`, `button-icon`,
`divider` …) are your team standard. A lean base definition lives in
assets/style.css and can be overridden by your central stylesheet.
"""
from __future__ import annotations

from dash import html

from config import IDS, APP_TITLE, APP_SUBTITLE, RESTRICTION_TEXT, LOGO_SRC


def _icon_button(btn_id: str, icon: str, title: str) -> html.Li:
    """An icon button inside an <li> -- fits into the header's icon-list."""
    return html.Li(
        html.Button(
            html.I(icon, className="material-icons-outlined"),
            id=btn_id,
            n_clicks=0,
            title=title,
            className="button-icon",
        )
    )


def _divider() -> html.Div:
    return html.Div(className="divider divider-v")


def header_layout(
    title: str = APP_TITLE,
    subtitle: str = APP_SUBTITLE,
    logo_src: str = LOGO_SRC,
    restriction_text: str = RESTRICTION_TEXT,
    menu_btn_id: str = IDS.MENU_BTN,
    filter_btn_id: str = IDS.FILTER_BTN,
) -> html.Header:
    return html.Header(
        className="team-header dark",
        children=[
            # Restriction mini bar on top
            html.Div(restriction_text, className="restriction-header"),
            # Main row
            html.Div(
                className="main-header horizontal",
                children=[
                    # Navigation menu (burger) -> left nav sidebar
                    html.Div(
                        className="panel panel-content",
                        children=[
                            html.Ul(
                                className="icon-list",
                                children=[
                                    _icon_button(menu_btn_id, "menu",
                                                 "Navigationsmenü"),
                                ],
                            )
                        ],
                    ),
                    _divider(),
                    # Logo
                    html.Div(
                        className="panel panel-content",
                        children=[
                            html.Div(
                                className="logo-container",
                                children=[html.Img(src=logo_src, className="logo",
                                                   alt="Logo")],
                            )
                        ],
                    ),
                    _divider(),
                    # Title + subtitle
                    html.Div(
                        className="panel panel-fixed-300",
                        children=[
                            html.Div(
                                className="title-container",
                                children=[
                                    html.H1(title, className="dashboard-title"),
                                    html.H2(subtitle, className="page-title"),
                                ],
                            )
                        ],
                    ),
                    # Filler pushes the right-hand side to the end
                    html.Div(className="panel panel-stretch"),
                    _divider(),
                    # Filter menu -> right filter sidebar
                    html.Div(
                        className="panel panel-content",
                        children=[
                            html.Ul(
                                className="icon-list",
                                children=[
                                    _icon_button(filter_btn_id, "filter_alt",
                                                 "Globale Filter"),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
