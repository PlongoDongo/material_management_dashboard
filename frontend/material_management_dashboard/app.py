"""
Material Management Dashboard -- entry point.

Architecture in brief
=====================
Single-page app with a shared "chrome" (header, both sidebars, footer tabs)
in the top-level layout. Only the tab content is shown/hidden via CSS.

    ┌──────────────────────── app.layout (never re-rendered) ───────────────┐
    │  Header (menu icon | title | filter icon)                             │
    │  ├─ left nav sidebar         (initially closed)                       │
    │  ├─ right filter sidebar     (initially closed, source of truth)      │
    │  ├─ MAIN                                                              │
    │  │    ├─ content-overview   (visible)   KPIs + table                  │
    │  │    ├─ content-manage     (hidden)                                  │
    │  │    └─ content-mappings   (hidden)                                  │
    │  ├─ footer tabs (Data overview | Manage data | Apply data mappings)   │
    │  └─ dcc.Store: store-filters, store-active-tab  (storage_type=session)│
    └───────────────────────────────────────────────────────────────────────┘

Why like this? -> Filters persist across tabs because nothing is unmounted.
Details/alternatives (incl. Plotly Pages) are described in README.md.
"""
from __future__ import annotations

from dash import Dash, dcc, html

from auth import register_auth
from config import IDS, APP_TITLE
from components.header_layout import header_layout
from components.nav_sidebar import nav_sidebar
from components.filter_sidebar import filter_sidebar
from components.footer_tabs import footer_tabs
from tabs.data_overview import data_overview_content
from tabs.manage_data import manage_data_content
from tabs.apply_mappings import apply_mappings_content
from callbacks.header_callbacks import register_header_callbacks
from callbacks.filter_callbacks import register_filter_callbacks
from callbacks.tab_callbacks import register_tab_callbacks
from callbacks.column_callbacks import register_column_callbacks
from kpi.kpi_rules import kpi_filter_map

# Load Material Icons as a ligature font. Icons are then referenced by name
# (html.I("menu", className="material-icons-outlined")) instead of as a
# copied special character -- more robust and consistent.
# Note for locked-down environments (proxy/offline): the font can also be
# self-hosted (put the file into assets/ and add @font-face to style.css).
MATERIAL_ICONS = (
    "https://fonts.googleapis.com/icon?family=Material+Icons+Outlined|Material+Icons"
)

app = Dash(__name__, title=APP_TITLE, suppress_callback_exceptions=True,
           external_stylesheets=[MATERIAL_ICONS])
server = app.server  # for Gunicorn / deployment

# Sign-in against Keycloak. It hangs off the Flask server, not off Dash: Dash
# IS a Flask application, and a `before_request` guard therefore also kicks in
# before every callback -- not just on the first page load.
# Without KEYCLOAK_ISSUER the sign-in stays off (development).
register_auth(server)

# No database driver any more: the dashboard talks exclusively over HTTP with
# the API layer (data/repository.py -> data/api_client.py). That way neither
# credentials nor Cypher live in this application.
# Configuration: DATA_API_URL (default http://localhost:8000), optionally
# DATA_API_KEY and DATA_CACHE_TTL.


def serve_layout() -> html.Div:
    """Evaluated on every page load -> KPIs always show fresh data."""
    # Tab contents: overview visible initially, the others hidden.
    overview = data_overview_content()
    manage = manage_data_content()
    mappings = apply_mappings_content()
    manage.style = {"display": "none"}
    mappings.style = {"display": "none"}

    return html.Div(
        className="app-shell",
        children=[
            # ---- shared state ----
            dcc.Store(id=IDS.STORE_FILTERS, storage_type="session"),
            dcc.Store(id=IDS.STORE_ACTIVE_TAB, storage_type="session",
                      data=IDS.TAB_OVERVIEW),
            # Set by assets/empty_click.js (click on an empty area).
            # Deliberately "memory": a click is an event, not a state.
            dcc.Store(id=IDS.STORE_EMPTY_CLICK),
            # Static rule table for assets/kpi_highlight.js. This way the
            # browser knows the KPI filters without asking the server for them
            # -- the rule itself stays in kpi/kpi_rules.py.
            dcc.Store(id=IDS.STORE_KPI_FILTERS, data=kpi_filter_map()),
            # ---- shared chrome ----
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

# ---- register callbacks ----
register_header_callbacks(app)   # sidebar toggles (menu/filter icon)
register_filter_callbacks(app)   # filters, KPI click, table
register_tab_callbacks(app)      # tab switching
register_column_callbacks(app)   # column selection popover (client-side)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)
