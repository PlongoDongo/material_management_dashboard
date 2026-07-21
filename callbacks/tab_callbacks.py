"""
Tab-Callbacks: Umschalten der drei Footer-Tabs.

Ansatz: Alle drei Tab-Inhalte liegen dauerhaft im DOM; wir schalten nur die
Sichtbarkeit per `display`-Style um. Das ist der robusteste Weg, weil so alle
Callback-Ziele (v. a. die Tabelle) stets existieren und der Filterzustand
beim Zurückwechseln sofort greift.

Persistenz der Filter über Tabs
-------------------------------
Weil Header, beide Sidebars UND alle Tab-Inhalte im selben, nie neu
gerenderten Top-Level-Layout hängen, gehen weder die Filter-Steuerelemente
noch der `store-filters`-Zustand beim Tab-Wechsel verloren. Genau das ist der
Grund, warum wir hier NICHT mit Plotly Pages (URL-Routing) arbeiten -- dort
würde der Seiteninhalt bei jedem Wechsel ausgetauscht (siehe README).
"""
from __future__ import annotations

from dash import Input, Output

from config import IDS, TABS

_VISIBLE = {"display": "block"}
_HIDDEN = {"display": "none"}

# Zuordnung Tab-ID -> Inhaltscontainer-ID
_TAB_CONTENT = {
    IDS.TAB_OVERVIEW: IDS.CONTENT_OVERVIEW,
    IDS.TAB_MANAGE: IDS.CONTENT_MANAGE,
    IDS.TAB_MAPPINGS: IDS.CONTENT_MAPPINGS,
}


def register_tab_callbacks(app) -> None:

    @app.callback(
        Output(IDS.STORE_ACTIVE_TAB, "data"),
        Output(IDS.CONTENT_OVERVIEW, "style"),
        Output(IDS.CONTENT_MANAGE, "style"),
        Output(IDS.CONTENT_MAPPINGS, "style"),
        Output(IDS.TAB_OVERVIEW, "className"),
        Output(IDS.TAB_MANAGE, "className"),
        Output(IDS.TAB_MAPPINGS, "className"),
        Input(IDS.TAB_OVERVIEW, "n_clicks"),
        Input(IDS.TAB_MANAGE, "n_clicks"),
        Input(IDS.TAB_MAPPINGS, "n_clicks"),
        prevent_initial_call=True,
    )
    def switch_tab(_a, _b, _c):
        from dash import ctx
        active = ctx.triggered_id or IDS.TAB_OVERVIEW

        styles = [
            _VISIBLE if _TAB_CONTENT[IDS.TAB_OVERVIEW] == _TAB_CONTENT[active] else _HIDDEN,
            _VISIBLE if _TAB_CONTENT[IDS.TAB_MANAGE] == _TAB_CONTENT[active] else _HIDDEN,
            _VISIBLE if _TAB_CONTENT[IDS.TAB_MAPPINGS] == _TAB_CONTENT[active] else _HIDDEN,
        ]
        classes = [
            "footer-tab" + (" active" if tab_id == active else "")
            for tab_id, _ in TABS
        ]
        return active, *styles, *classes
