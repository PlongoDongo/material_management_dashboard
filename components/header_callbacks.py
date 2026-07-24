"""
Header-Callbacks: Öffnen/Schließen der beiden Sidebars.

Bewusst als `register_callbacks(app)`-Funktion, damit dieselbe Header-Logik
in mehreren Apps/Dashboards wiederverwendet werden kann (genau wie bei deinem
vorherigen Dashboard). In app.py rufst du einmal
`register_header_callbacks(app)` auf.

Toggle-Muster
-------------
Statt einen booleschen Zustand zu speichern, leiten wir die Sichtbarkeit aus
der CSS-Klasse ab (`... open`). Ein einziger Callback pro Sidebar reagiert auf
alle relevanten Trigger (Icon, Overlay, Schließen-Button) und entscheidet über
`dash.ctx`, ob geöffnet oder geschlossen wird. Das ist robust und kommt ohne
zusätzlichen Store aus.
"""
from __future__ import annotations

from dash import Input, Output, State, ctx

from config import IDS


def register_header_callbacks(app) -> None:

    # ---- Linke Navigations-Sidebar ---------------------------------------
    @app.callback(
        Output(IDS.NAV_SIDEBAR, "className"),
        Output(IDS.NAV_OVERLAY, "className"),
        Input(IDS.MENU_BTN, "n_clicks"),
        Input(IDS.NAV_OVERLAY, "n_clicks"),
        Input(IDS.NAV_CLOSE, "n_clicks"),
        State(IDS.NAV_SIDEBAR, "className"),
        prevent_initial_call=True,
    )
    def toggle_nav(_menu, _overlay, _close, current_cls):
        trigger = ctx.triggered_id
        is_open = "open" in (current_cls or "")
        # Menü-Icon = umschalten; Overlay/Schließen = immer schließen
        if trigger == IDS.MENU_BTN:
            is_open = not is_open
        else:
            is_open = False
        return _classes("sidebar sidebar-nav", "sidebar-overlay", is_open)

    # ---- Rechte Filter-Sidebar -------------------------------------------
    # Wird nur noch über das Filter-Icon im Header geöffnet. Der frühere
    # "Filter"-Button an der Tabelle steuert jetzt die Spaltenauswahl
    # (tabs/data_overview.py) und nicht mehr diese globale Sidebar.
    @app.callback(
        Output(IDS.FILTER_SIDEBAR, "className"),
        Output(IDS.FILTER_OVERLAY, "className"),
        Input(IDS.FILTER_BTN, "n_clicks"),
        Input(IDS.FILTER_OVERLAY, "n_clicks"),
        Input(IDS.FILTER_CLOSE, "n_clicks"),
        State(IDS.FILTER_SIDEBAR, "className"),
        prevent_initial_call=True,
    )
    def toggle_filter(_icon, _overlay, _close, current_cls):
        trigger = ctx.triggered_id
        is_open = "open" in (current_cls or "")
        if trigger == IDS.FILTER_BTN:
            is_open = not is_open
        else:
            is_open = False
        return _classes("sidebar sidebar-filter", "sidebar-overlay", is_open)


def _classes(sidebar_base: str, overlay_base: str, is_open: bool):
    if is_open:
        return f"{sidebar_base} open", f"{overlay_base} open"
    return sidebar_base, overlay_base
