"""
Wiederverwendbarer Header (Team-Standard).

Aufbau (Reihenfolge wie gehabt):

    ┌─ Restriction-Mini-Leiste ("Restricted") ─────────────────────────────┐
    ├───────────────────────────────────────────────────────────────────────┤
    │ [☰ Menü] │ Logo │ Titel/Untertitel        …Filler…        │ [⛃ Filter] │
    └───────────────────────────────────────────────────────────────────────┘

Wiederverwendung in anderen Dashboards
--------------------------------------
`header_layout()` ist bewusst parametrisiert -- Titel, Untertitel, Logo und
Restriction-Text kommen als Argumente herein, die Button-IDs sind einstellbar.
Ein neues Dashboard ruft einfach `header_layout(title=..., subtitle=...)` auf;
die zugehörigen Öffnen/Schließen-Callbacks stehen in
`callbacks/header_callbacks.py`.

Der Header enthält bewusst KEINE Sidebars mehr. Nav- und Filter-Sidebar sind
eigene Komponenten (components/nav_sidebar.py, filter_sidebar.py) und liegen
im Top-Level-Layout. So bleibt der Header schlank und in jedem Dashboard
gleich, während jede App ihre eigenen Sidebars daneben hängt.

Icons
-----
Material Icons werden über ein Stylesheet (in app.py als external_stylesheet
verlinkt) geladen und per Ligatur-Namen referenziert -- also
`html.I("menu", className="material-icons-outlined")` statt eines
Sonderzeichens im String. Damit hängt die Darstellung nicht an einem
kopierten Glyphen und bleibt konsistent.

Styling
-------
Die verwendeten Klassen (`team-header`, `main-header`, `panel`, `button-icon`,
`divider` …) sind euer Team-Standard. Eine schlanke Basis-Definition liegt in
assets/style.css und kann von eurem zentralen Stylesheet überschrieben werden.
"""
from __future__ import annotations

from dash import html

from config import IDS, APP_TITLE, APP_SUBTITLE, RESTRICTION_TEXT, LOGO_SRC


def _icon_button(btn_id: str, icon: str, title: str) -> html.Li:
    """Ein Icon-Button in einer <li> -- passt in die icon-list des Headers."""
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
            # Restriction-Mini-Leiste darüber
            html.Div(restriction_text, className="restriction-header"),
            # Hauptzeile
            html.Div(
                className="main-header horizontal",
                children=[
                    # Navigationsmenü (Burger) -> linke Nav-Sidebar
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
                    # Titel + Untertitel
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
                    # Filler schiebt die rechte Seite ans Ende
                    html.Div(className="panel panel-stretch"),
                    _divider(),
                    # Filtermenü -> rechte Filter-Sidebar
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
