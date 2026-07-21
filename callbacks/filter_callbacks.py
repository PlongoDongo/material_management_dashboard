"""
Filter-Callbacks -- das Herzstück der Filter-/KPI-Interaktion.

Datenfluss (bewusst zyklusfrei):

    [Filter-Steuerelemente]  ──►  store-filters  ──►  [Tabelle + Zähler]
            ▲   ▲   ▲                      │
            │   │   │                      └─────►  [aktive KPI-Kachel]
            │   │   └──── Klick auf KPI-Kachel  (setzt Status/Flag,
            │   │                                erneuter Klick hebt auf)
            │   └──────── Klick auf leere Fläche (hebt den KPI-Filter auf)
            └──────────── "Zurücksetzen"-Button (leert ALLE Steuerelemente)

Die Steuerelemente in der rechten Sidebar sind die einzige Wahrheitsquelle
des Filters. KPI-Klick und Reset schreiben NUR in diese Steuerelemente; von
dort fließt es weiter in den Store und in die Tabelle. Dadurch gibt es keinen
Rückkanal Store -> Steuerelement und damit keinen Callback-Zyklus.

Der Store fließt zusätzlich in die *Darstellung* der Kacheln (welche ist
aktiv?) -- das ist eine Sackgasse und schließt den Kreis nicht.
"""
from __future__ import annotations

from dash import ALL, Input, Output, State, ctx, no_update

from config import IDS
from data.filtering import apply_filters
from data.repository import get_materials
from kpi.kpi_rules import KPI_DEFINITIONS

# Schneller Lookup: KPI-ID -> Filter-Update
_KPI_FILTER = {k["id"]: k["filter"] for k in KPI_DEFINITIONS}


def _kpi_is_active(kpi_id: str, status, ohne_klass) -> bool:
    """Greift der Filter dieser Kachel gerade genau so, wie sie ihn setzen würde?

    Die Aktivität wird bewusst aus dem GEGENWÄRTIGEN Filterzustand abgeleitet
    und nicht separat gespeichert. So bleibt es korrekt, wenn der User den
    Status stattdessen von Hand in der Sidebar ändert -- und die Architektur
    bleibt zyklusfrei (kein Rückkanal Store -> Steuerelement).

    Werk / Warengruppe / Suche bleiben außen vor: die Kacheln steuern nur die
    Dimensionen Status und "ohne Klassifizierung".
    """
    flt = _KPI_FILTER.get(kpi_id, {})
    return (
        set(status or []) == set(flt.get("status", []))
        and bool(ohne_klass) == bool(flt.get("ohne_klass"))
    )


def register_filter_callbacks(app) -> None:

    # ---------------------------------------------------------------
    # 1) Klick auf eine KPI-Kachel  ->  Filter setzen ODER (bei erneutem
    #    Klick auf die bereits aktive Kachel) wieder aufheben.
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.F_STATUS, "value", allow_duplicate=True),
        Output(IDS.F_OHNE_KLASS, "value", allow_duplicate=True),
        Input({"type": "kpi-tile", "kpi": ALL}, "n_clicks"),
        State(IDS.F_STATUS, "value"),
        State(IDS.F_OHNE_KLASS, "value"),
        prevent_initial_call=True,
    )
    def kpi_click_to_filter(_clicks, cur_status, cur_ohne_klass):
        trigger = ctx.triggered_id
        if not trigger or "kpi" not in trigger:
            return no_update, no_update

        # Toggle: dieselbe Kachel erneut -> Status-/Klassifizierungsfilter leeren.
        if _kpi_is_active(trigger["kpi"], cur_status, cur_ohne_klass):
            return [], []

        flt = _KPI_FILTER.get(trigger["kpi"], {})
        status_value = list(flt.get("status", []))
        ohne_klass_value = ["on"] if flt.get("ohne_klass") else []
        return status_value, ohne_klass_value

    # ---------------------------------------------------------------
    # 1b) Klick auf eine leere Fläche im Tab-Bereich  ->  KPI-Filter aufheben.
    #     Der Store wird von assets/empty_click.js gesetzt; dort steckt die
    #     Prüfung, ob wirklich "ins Leere" geklickt wurde.
    #
    #     Bewusst NUR Status + "ohne Klassifizierung": Suche, Werk und
    #     Warengruppe hat der User explizit in der Sidebar gesetzt -- die
    #     räumt weiterhin nur "Filter zurücksetzen" ab.
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.F_STATUS, "value", allow_duplicate=True),
        Output(IDS.F_OHNE_KLASS, "value", allow_duplicate=True),
        Input(IDS.STORE_EMPTY_CLICK, "data"),
        State(IDS.F_STATUS, "value"),
        State(IDS.F_OHNE_KLASS, "value"),
        prevent_initial_call=True,
    )
    def empty_click_clears_kpi_filter(_ts, cur_status, cur_ohne_klass):
        # Nichts aktiv -> nichts tun (spart einen überflüssigen Tabellen-Rerender).
        if not cur_status and not cur_ohne_klass:
            return no_update, no_update
        return [], []

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

    # ---------------------------------------------------------------
    # 5) Filterzustand  ->  aktive KPI-Kachel hervorheben
    #    Reine Anzeige (Store -> className), schreibt nicht in die
    #    Steuerelemente zurück -> kein Zyklus. Macht den Toggle sichtbar.
    # ---------------------------------------------------------------
    @app.callback(
        Output({"type": "kpi-tile", "kpi": ALL}, "className"),
        Input(IDS.STORE_FILTERS, "data"),
    )
    def highlight_active_kpi(filters):
        filters = filters or {}
        status = filters.get("status") or []
        ohne_klass = filters.get("ohne_klass")
        return [
            "kpi-tile kpi-tile--active"
            if _kpi_is_active(spec["id"]["kpi"], status, ohne_klass)
            else "kpi-tile"
            for spec in ctx.outputs_list
        ]
