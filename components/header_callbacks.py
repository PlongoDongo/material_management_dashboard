"""
Header-Callbacks: Öffnen/Schließen der beiden Sidebars.

Bewusst als `register_header_callbacks(app)`-Funktion, damit dieselbe Header-
Logik in mehreren Apps/Dashboards wiederverwendet werden kann. In app.py ruft
man einmal `register_header_callbacks(app)` auf.

Warum clientseitig?
-------------------
Es ist reine Darstellung -- die Sidebar (und ihr Overlay) bekommen bzw.
verlieren nur die CSS-Klasse `open`. Dafür braucht es keinen Server. Früher lief
das als Server-Callback und kostete pro Klick eine HTTP-Runde, bevor die
Animation überhaupt anlief; bei hoher Netz-Latenz war das spürbar träge. Jetzt
schaltet es im Browser sofort um (assets/sidebar_toggle.js), sichtbar bleibt nur
noch die CSS-Transition.

Toggle-Muster
-------------
Statt einen booleschen Zustand zu speichern, leiten wir die Sichtbarkeit aus der
CSS-Klasse ab (`... open`). Ein einziger Callback pro Sidebar reagiert auf alle
relevanten Trigger (Icon, Overlay, Schließen-Button) und entscheidet über den
`callback_context`, ob geöffnet oder geschlossen wird.
"""
from __future__ import annotations

from dash import ClientsideFunction, Dash, Input, Output, State

from config import IDS


def register_header_callbacks(app: Dash) -> None:

    # ---- Linke Navigations-Sidebar ---------------------------------------
    app.clientside_callback(
        ClientsideFunction(namespace="sidebar", function_name="toggleNav"),
        Output(IDS.NAV_SIDEBAR, "className"),
        Output(IDS.NAV_OVERLAY, "className"),
        Input(IDS.MENU_BTN, "n_clicks"),
        Input(IDS.NAV_OVERLAY, "n_clicks"),
        Input(IDS.NAV_CLOSE, "n_clicks"),
        State(IDS.NAV_SIDEBAR, "className"),
        prevent_initial_call=True,
    )

    # ---- Rechte Filter-Sidebar -------------------------------------------
    # Wird nur über das Filter-Icon im Header geöffnet. Der frühere "Filter"-
    # Button an der Tabelle steuert jetzt die Spaltenauswahl (data_overview.py).
    app.clientside_callback(
        ClientsideFunction(namespace="sidebar", function_name="toggleFilter"),
        Output(IDS.FILTER_SIDEBAR, "className"),
        Output(IDS.FILTER_OVERLAY, "className"),
        Input(IDS.FILTER_BTN, "n_clicks"),
        Input(IDS.FILTER_OVERLAY, "n_clicks"),
        Input(IDS.FILTER_CLOSE, "n_clicks"),
        State(IDS.FILTER_SIDEBAR, "className"),
        prevent_initial_call=True,
    )
