"""
Filter-Callbacks -- das Herzstück der Filter-/KPI-Interaktion.

Datenfluss (bewusst zyklusfrei):

    [Filter-Steuerelemente]  ──►  store-filters  ──►  [Tabelle + Zähler]
            ▲   ▲
            │   └──── Klick auf KPI-Kachel  (setzt Status/Flag)
            └──────── "Zurücksetzen"-Button (leert alle Steuerelemente)

Die Steuerelemente in der rechten Sidebar sind die einzige Wahrheitsquelle
des Filters. KPI-Klick und Reset schreiben NUR in diese Steuerelemente; von
dort fließt es weiter in den Store und in die Tabelle. Dadurch gibt es keinen
Rückkanal Store -> Steuerelement und damit keinen Callback-Zyklus.
"""
from __future__ import annotations

from dash import ALL, Input, Output, ctx

from config import IDS
from data.filtering import apply_filters
from data.repository import get_materials
from kpi.kpi_rules import KPI_DEFINITIONS

# Schneller Lookup: KPI-ID -> Filter-Update
_KPI_FILTER = {k["id"]: k["filter"] for k in KPI_DEFINITIONS}


def register_filter_callbacks(app) -> None:

    # ---------------------------------------------------------------
    # 1) Klick auf eine KPI-Kachel  ->  setzt Status + "ohne Klass."-Flag
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.F_STATUS, "value", allow_duplicate=True),
        Output(IDS.F_OHNE_KLASS, "value", allow_duplicate=True),
        Input({"type": "kpi-tile", "kpi": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def kpi_click_to_filter(_clicks):
        trigger = ctx.triggered_id
        if not trigger or "kpi" not in trigger:
            from dash import no_update
            return no_update, no_update
        flt = _KPI_FILTER.get(trigger["kpi"], {})
        status_value = list(flt.get("status", []))
        ohne_klass_value = ["on"] if flt.get("ohne_klass") else []
        return status_value, ohne_klass_value

    # ---------------------------------------------------------------
    # 2) "Filter zurücksetzen"  ->  leert alle Steuerelemente
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.F_STATUS, "value", allow_duplicate=True),
        Output(IDS.F_WERK, "value"),
        Output(IDS.F_WARENGRUPPE, "value"),
        Output(IDS.F_SEARCH, "value"),
        Output(IDS.F_OHNE_KLASS, "value", allow_duplicate=True),
        Input(IDS.F_RESET, "n_clicks"),
        prevent_initial_call=True,
    )
    def reset_filters(_n):
        return [], [], [], "", []

    # ---------------------------------------------------------------
    # 3) Steuerelemente  ->  kanonischer Filterzustand (Store)
    #    Läuft auch beim Laden (prevent_initial_call=False), damit der
    #    Store initial korrekt gefüllt ist.
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.STORE_FILTERS, "data"),
        Input(IDS.F_STATUS, "value"),
        Input(IDS.F_WERK, "value"),
        Input(IDS.F_WARENGRUPPE, "value"),
        Input(IDS.F_SEARCH, "value"),
        Input(IDS.F_OHNE_KLASS, "value"),
    )
    def build_filter_state(status, werk, warengruppe, search, ohne_klass):
        return {
            "status": status or [],
            "werk": werk or [],
            "warengruppe": warengruppe or [],
            "search": search or "",
            "ohne_klass": bool(ohne_klass),  # ["on"] -> True, [] -> False
        }

    # ---------------------------------------------------------------
    # 4) Filterzustand  ->  gefilterte Tabelle + Datensatz-Zähler
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.TABLE, "data"),
        Output(IDS.RECORD_COUNTER, "children"),
        Input(IDS.STORE_FILTERS, "data"),
    )
    def render_table(filters):
        df_all = get_materials()
        df = apply_filters(df_all, filters)
        counter = f"{df.height} / {df_all.height} Datensätze"
        return df.to_dicts(), counter
