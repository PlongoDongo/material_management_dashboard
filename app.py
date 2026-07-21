"""
Material Management Dashboard -- Einstiegspunkt.

Architektur in Kürze
====================
Single-Page-App mit geteiltem "Chrome" (Header, beide Sidebars, Footer-Tabs)
im Top-Level-Layout. Nur der Tab-Inhalt wird per CSS ein-/ausgeblendet.

    ┌──────────────────────── app.layout (nie neu gerendert) ───────────────┐
    │  Header (Menü-Icon | Titel | Filter-Icon)                             │
    │  ├─ linke Nav-Sidebar        (initial geschlossen)                     │
    │  ├─ rechte Filter-Sidebar    (initial geschlossen, Wahrheitsquelle)    │
    │  ├─ MAIN                                                               │
    │  │    ├─ content-overview   (sichtbar)   KPIs + Tabelle                │
    │  │    ├─ content-manage     (hidden)                                   │
    │  │    └─ content-mappings   (hidden)                                   │
    │  ├─ Footer-Tabs (Data overview | Manage data | Apply data mappings)    │
    │  └─ dcc.Store: store-filters, store-active-tab  (storage_type=session) │
    └───────────────────────────────────────────────────────────────────────┘

Warum so? -> Filter persistieren über Tabs, weil nichts unmountet wird.
Details/Alternativen (inkl. Plotly Pages) stehen im README.md.
"""
from __future__ import annotations

from dash import Dash, dcc, html

from config import IDS, APP_TITLE
from components.header_layout import header_layout
from components.header_callbacks import register_header_callbacks
from components.nav_sidebar import nav_sidebar
from components.filter_sidebar import filter_sidebar
from components.footer_tabs import footer_tabs
from tabs.data_overview import data_overview_content
from tabs.manage_data import manage_data_content
from tabs.apply_mappings import apply_mappings_content
from callbacks.filter_callbacks import register_filter_callbacks
from callbacks.tab_callbacks import register_tab_callbacks

app = Dash(__name__, title=APP_TITLE, suppress_callback_exceptions=True)
server = app.server  # für Gunicorn / Deployment


def serve_layout() -> html.Div:
    """Wird bei jedem Seitenaufruf ausgewertet -> KPIs zeigen stets frische Daten."""
    # Tab-Inhalte: Overview initial sichtbar, die anderen ausgeblendet.
    overview = data_overview_content()
    manage = manage_data_content()
    mappings = apply_mappings_content()
    manage.style = {"display": "none"}
    mappings.style = {"display": "none"}

    return html.Div(
        className="app-shell",
        children=[
            # ---- geteilter State ----
            dcc.Store(id=IDS.STORE_FILTERS, storage_type="session"),
            dcc.Store(id=IDS.STORE_ACTIVE_TAB, storage_type="session",
                      data=IDS.TAB_OVERVIEW),
            # Wird von assets/empty_click.js gesetzt (Klick auf leere Fläche).
            # Bewusst "memory": ein Klick ist ein Ereignis, kein Zustand.
            dcc.Store(id=IDS.STORE_EMPTY_CLICK),
            # ---- geteiltes Chrome ----
            header_layout(),
            nav_sidebar(),
            filter_sidebar(),
            html.Main(
                className="app-main",
                children=[overview, manage, mappings],
            ),
            footer_tabs(active_tab=IDS.TAB_OVERVIEW),
        ],
    )


app.layout = serve_layout

# ---- Callbacks registrieren ----
register_header_callbacks(app)   # Sidebar-Toggles (Menü-/Filter-Icon)
register_filter_callbacks(app)   # Filter, KPI-Klick, Tabelle
register_tab_callbacks(app)      # Tab-Umschaltung


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)
