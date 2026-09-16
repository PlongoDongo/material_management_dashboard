"""
Filter callbacks -- the heart of the filter/KPI interaction.

Data flow (deliberately free of cycles):

                                     ┌──►  store-filters   (canonical, session)
                                     │
    [filter controls]  ──────────────┼──►  [table + record counter]
         ▲   ▲   ▲                   │
         │   │   │                   └──►  [KPI tiles active/inactive]  ← in the browser
         │   │   └──── click on a KPI tile   (sets status/flag,
         │   │                                clicking again clears it)
         │   └──────── click on empty space  (clears the KPI filter)
         └──────────── "Zurücksetzen" button (clears ALL controls)

The controls in the right-hand sidebar are the single source of truth for the
filter. A KPI click and the reset write ONLY into those controls; from there it
flows onwards. That way there is no back channel into the controls, and hence
no callback cycle.

On latency: store, table and tiles all hang DIRECTLY off the controls and
therefore run in parallel. It used to be a chain (control -> store -> table ->
tiles); every stage cost a server round trip of its own, which added up to a
noticeable delay on each click. The tile highlighting is pure presentation and
runs client-side -- that is, without any server round trip at all
(assets/kpi_highlight.js).
"""
from __future__ import annotations

from typing import Any

from dash import (
    ALL,
    ClientsideFunction,
    Dash,
    Input,
    NoUpdate,
    Output,
    State,
    ctx,
    no_update,
)

from config import IDS
from data.filtering import apply_filters
from data.repository import get_materials
from kpi.kpi_rules import kpi_filter_map

# Value of a filter control as a callback returns it: the new selection -- or
# `no_update` when the control is to be left untouched.
Selection = list[str] | NoUpdate

# Fast lookup: KPI id -> filter update
_KPI_FILTER = kpi_filter_map()

# The five controls the filter is made up of. Store and table both hang
# directly off these so that they run in parallel.
_FILTER_INPUTS = (
    Input(IDS.F_STATUS, "value"),
    Input(IDS.F_PLANT, "value"),
    Input(IDS.F_MATERIAL_GROUP, "value"),
    Input(IDS.F_SEARCH, "value"),
    Input(IDS.F_OHNE_KLASS, "value"),
)


def filter_state(
    status: list[str] | None,
    plant: list[str] | None,
    material_group: list[str] | None,
    search: str | None,
    ohne_klass: list[str] | None,
) -> dict:
    """Control values -> canonical filter state.

    One function for both consumers (store and table), so that the
    normalisation cannot drift apart.
    """
    return {
        "status": status or [],
        "plant_name": plant or [],
        "material_group": material_group or [],
        "search": search or "",
        "ohne_klass": bool(ohne_klass),  # ["on"] -> True, [] -> False
    }


def _kpi_is_active(
    kpi_id: str, status: list[str] | None, ohne_klass: list[str] | None
) -> bool:
    """Is this tile's filter currently in effect exactly as the tile would set it?

    Whether a tile is active is deliberately derived from the CURRENT filter
    state instead of being stored separately. That keeps it correct when the
    user changes the status by hand in the sidebar instead -- and it keeps the
    architecture free of cycles (no back channel store -> control).

    Plant / material group / search stay out of it: the tiles only drive the
    status and "without classification" dimensions.
    """
    flt = _KPI_FILTER.get(kpi_id, {})
    return (
        set(status or []) == set(flt.get("status", []))
        and bool(ohne_klass) == bool(flt.get("ohne_klass"))
    )


def register_filter_callbacks(app: Dash) -> None:

    # ---------------------------------------------------------------
    # 1) Click on a KPI tile  ->  set the filter OR (when the already
    #    active tile is clicked again) clear it.
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.F_STATUS, "value", allow_duplicate=True),
        Output(IDS.F_OHNE_KLASS, "value", allow_duplicate=True),
        Input({"type": "kpi-tile", "kpi": ALL}, "n_clicks"),
        State(IDS.F_STATUS, "value"),
        State(IDS.F_OHNE_KLASS, "value"),
        prevent_initial_call=True,
    )
    def kpi_click_to_filter(
        _clicks: list[int | None],
        cur_status: list[str] | None,
        cur_ohne_klass: list[str] | None,
    ) -> tuple[Selection, Selection]:
        trigger = ctx.triggered_id
        if not trigger or "kpi" not in trigger:
            return no_update, no_update

        # Toggle: the same tile again -> clear the status/classification filter.
        if _kpi_is_active(trigger["kpi"], cur_status, cur_ohne_klass):
            return [], []

        flt = _KPI_FILTER.get(trigger["kpi"], {})
        status_value = list(flt.get("status", []))
        ohne_klass_value = ["on"] if flt.get("ohne_klass") else []
        return status_value, ohne_klass_value

    # ---------------------------------------------------------------
    # 1b) Click on empty space inside the tab area  ->  clear the KPI filter.
    #     The store is set by assets/empty_click.js; that is where the check
    #     lives for whether the click really landed "on nothing".
    #
    #     Deliberately ONLY status + "without classification": search, plant
    #     and material group were set explicitly by the user in the sidebar --
    #     those are still only cleared by "Filter zurücksetzen".
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.F_STATUS, "value", allow_duplicate=True),
        Output(IDS.F_OHNE_KLASS, "value", allow_duplicate=True),
        Input(IDS.STORE_EMPTY_CLICK, "data"),
        State(IDS.F_STATUS, "value"),
        State(IDS.F_OHNE_KLASS, "value"),
        prevent_initial_call=True,
    )
    def empty_click_clears_kpi_filter(
        _ts: Any,
        cur_status: list[str] | None,
        cur_ohne_klass: list[str] | None,
    ) -> tuple[Selection, Selection]:
        # Nothing active -> do nothing (saves a pointless table re-render).
        if not cur_status and not cur_ohne_klass:
            return no_update, no_update
        return [], []

    # ---------------------------------------------------------------
    # 2) "Filter zurücksetzen"  ->  clears all controls
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.F_STATUS, "value", allow_duplicate=True),
        Output(IDS.F_PLANT, "value"),
        Output(IDS.F_MATERIAL_GROUP, "value"),
        Output(IDS.F_SEARCH, "value"),
        Output(IDS.F_OHNE_KLASS, "value", allow_duplicate=True),
        Input(IDS.F_RESET, "n_clicks"),
        prevent_initial_call=True,
    )
    def reset_filters(
        _n: int | None,
    ) -> tuple[list[str], list[str], list[str], str, list[str]]:
        return [], [], [], "", []

    # ---------------------------------------------------------------
    # 3) Controls  ->  canonical filter state (store)
    #    Runs on page load as well (prevent_initial_call=False) so that the
    #    store is filled correctly right from the start.
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.STORE_FILTERS, "data"),
        *_FILTER_INPUTS,
    )
    def build_filter_state(
        status: list[str] | None,
        plant: list[str] | None,
        material_group: list[str] | None,
        search: str | None,
        ohne_klass: list[str] | None,
    ) -> dict:
        return filter_state(status, plant, material_group, search, ohne_klass)

    # ---------------------------------------------------------------
    # 4) Controls  ->  filtered table + record counter
    #
    #    Deliberately hangs off the controls and NOT off the store: otherwise
    #    the chain click -> filter -> store -> table would be three serial
    #    server round trips long. This way store and table run in parallel and
    #    the table is there one round trip earlier. `store-filters` remains the
    #    canonical, session-persistent state -- the table simply no longer
    #    waits for it.
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.TABLE, "data"),
        Output(IDS.RECORD_COUNTER, "children"),
        *_FILTER_INPUTS,
    )
    def render_table(
        status: list[str] | None,
        plant: list[str] | None,
        material_group: list[str] | None,
        search: str | None,
        ohne_klass: list[str] | None,
    ) -> tuple[list[dict], str]:
        filters = filter_state(status, plant, material_group, search, ohne_klass)
        df_all = get_materials()
        df = apply_filters(df_all, filters)
        counter = f"{df.height} / {df_all.height} Datensätze"
        return df.to_dicts(), counter

    # ---------------------------------------------------------------
    # 5) Controls  ->  active/inactive KPI tiles   (CLIENT-SIDE)
    #
    #    Pure presentation, so in the browser instead of on the server: the
    #    tiles switch over as soon as the click callback is back, without a
    #    further server round trip and without waiting for the DataTable to
    #    re-render. The rule (which filter belongs to which tile) is handed in
    #    by `store-kpi-filters` from kpi/kpi_rules.py.
    #    Implemented in assets/kpi_highlight.js
    # ---------------------------------------------------------------
    app.clientside_callback(
        ClientsideFunction(namespace="kpi", function_name="highlight"),
        Output({"type": "kpi-tile", "kpi": ALL}, "className"),
        Input(IDS.F_STATUS, "value"),
        Input(IDS.F_OHNE_KLASS, "value"),
        State(IDS.STORE_KPI_FILTERS, "data"),
    )
