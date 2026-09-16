"""
Header callbacks: opening/closing the two sidebars.

Deliberately written as a `register_header_callbacks(app)` function so that the
same header logic can be reused across several apps/dashboards. In app.py you
call `register_header_callbacks(app)` once.

Why client-side?
----------------
It is pure presentation -- the sidebar (and its overlay) merely gain or lose the
CSS class `open`. No server is needed for that. It used to run as a server
callback and cost one HTTP round trip per click before the animation even
started; with high network latency that felt noticeably sluggish. Now it
switches over immediately in the browser (assets/sidebar_toggle.js), and the
only thing still visible is the CSS transition.

Toggle pattern
--------------
Instead of storing a boolean state, we derive the visibility from the CSS class
(`... open`). A single callback per sidebar reacts to all relevant triggers
(icon, overlay, close button) and decides via the `callback_context` whether to
open or to close.
"""
from __future__ import annotations

from dash import ClientsideFunction, Dash, Input, Output, State

from config import IDS


def register_header_callbacks(app: Dash) -> None:

    # ---- Left navigation sidebar -----------------------------------------
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

    # ---- Right filter sidebar --------------------------------------------
    # Only ever opened via the filter icon in the header. The former "Filter"
    # button on the table now drives the column selection (data_overview.py).
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
