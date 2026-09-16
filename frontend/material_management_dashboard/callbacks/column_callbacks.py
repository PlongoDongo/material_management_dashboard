"""
Column selection for the material table -- purely client-side.

Why client-side? It is pure presentation (which columns the DataTable shows)
and needs no data from the server. That way the selection switches over without
a server round trip -- consistent with the KPI highlighting
(assets/kpi_highlight.js).

Two callbacks:
  1. checklist   -> hidden_columns of the table (hidden = the unticked ones)
  2. "Alle"/"Keine" -> sets the checklist (which then chains into 1.)

Opening/closing the popover itself is not a Dash callback but a slim document
listener in assets/column_menu.js (clicking the button toggles it, clicking
next to it closes it).
"""
from __future__ import annotations

from dash import ClientsideFunction, Dash, Input, Output, State

from config import IDS


def register_column_callbacks(app: Dash) -> None:
    # 1) Ticked columns -> hidden_columns (all unticked toggleable ones)
    app.clientside_callback(
        ClientsideFunction(namespace="cols", function_name="applyVisibility"),
        Output(IDS.TABLE, "hidden_columns"),
        Input(IDS.COLS_CHECKLIST, "value"),
        State(IDS.COLS_CHECKLIST, "options"),
    )

    # 2) "Alle" / "Keine" -> set the checklist (triggers callback 1 afterwards)
    app.clientside_callback(
        ClientsideFunction(namespace="cols", function_name="selectAll"),
        Output(IDS.COLS_CHECKLIST, "value"),
        Input(IDS.COLS_ALL, "n_clicks"),
        Input(IDS.COLS_NONE, "n_clicks"),
        State(IDS.COLS_CHECKLIST, "options"),
        prevent_initial_call=True,
    )
