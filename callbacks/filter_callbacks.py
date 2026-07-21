"""
Filter-Callbacks -- das Herzstück der Filter-/KPI-Interaktion.

Datenfluss (bewusst zyklusfrei):

                                     ┌──►  store-filters   (kanonisch, session)
                                     │
    [Filter-Steuerelemente]  ─────────┼──►  [Tabelle + Zähler]
            ▲   ▲   ▲                │
            │   │   │                └──►  [KPI-Kacheln aktiv/inaktiv]  ← im Browser
            │   │   └──── Klick auf KPI-Kachel  (setzt Status/Flag,
            │   │                                erneuter Klick hebt auf)
            │   └──────── Klick auf leere Fläche (hebt den KPI-Filter auf)
            └──────────── "Zurücksetzen"-Button (leert ALLE Steuerelemente)

Die Steuerelemente in der rechten Sidebar sind die einzige Wahrheitsquelle
des Filters. KPI-Klick und Reset schreiben NUR in diese Steuerelemente; von
dort fließt es weiter. Dadurch gibt es keinen Rückkanal zurück in die
Steuerelemente und damit keinen Callback-Zyklus.

Zur Latenz: Store, Tabelle und Kacheln hängen alle DIREKT an den
Steuerelementen und laufen damit parallel. Früher war es eine Kette
(Steuerelement -> Store -> Tabelle -> Kacheln); jede Stufe kostete eine
eigene Server-Runde, was sich beim Klick als spürbare Verzögerung summierte.
Die Kachel-Hervorhebung ist reine Darstellung und läuft clientseitig -- also
ganz ohne Server-Runde (assets/kpi_highlight.js).
"""
from __future__ import annotations

from dash import ALL, ClientsideFunction, Input, Output, State, ctx, no_update

from config import IDS
from data.filtering import apply_filters
from data.repository import get_materials
from kpi.kpi_rules import kpi_filter_map

# Schneller Lookup: KPI-ID -> Filter-Update
_KPI_FILTER = kpi_filter_map()

# Die fünf Steuerelemente, aus denen sich der Filter zusammensetzt. Store und
# Tabelle hängen beide direkt hier dran, damit sie parallel laufen.
_FILTER_INPUTS = (
    Input(IDS.F_STATUS, "value"),
    Input(IDS.F_WERK, "value"),
    Input(IDS.F_WARENGRUPPE, "value"),
    Input(IDS.F_SEARCH, "value"),
    Input(IDS.F_OHNE_KLASS, "value"),
)


def filter_state(status, werk, warengruppe, search, ohne_klass) -> dict:
    """Steuerelement-Werte -> kanonischer Filterzustand.

    Eine Funktion für beide Verbraucher (Store und Tabelle), damit die
    Normalisierung nicht auseinanderlaufen kann.
    """
    return {
        "status": status or [],
        "werk": werk or [],
        "warengruppe": warengruppe or [],
        "search": search or "",
        "ohne_klass": bool(ohne_klass),  # ["on"] -> True, [] -> False
    }


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
        *_FILTER_INPUTS,
    )
    def build_filter_state(status, werk, warengruppe, search, ohne_klass):
        return filter_state(status, werk, warengruppe, search, ohne_klass)

    # ---------------------------------------------------------------
    # 4) Steuerelemente  ->  gefilterte Tabelle + Datensatz-Zähler
    #
    #    Hängt bewusst an den Steuerelementen und NICHT am Store: sonst
    #    wäre die Kette Klick -> Filter -> Store -> Tabelle drei serielle
    #    Server-Runden lang. So laufen Store und Tabelle parallel, und die
    #    Tabelle ist eine Runde früher da. `store-filters` bleibt der
    #    kanonische, session-persistente Zustand -- die Tabelle wartet nur
    #    nicht mehr darauf.
    # ---------------------------------------------------------------
    @app.callback(
        Output(IDS.TABLE, "data"),
        Output(IDS.RECORD_COUNTER, "children"),
        *_FILTER_INPUTS,
    )
    def render_table(status, werk, warengruppe, search, ohne_klass):
        filters = filter_state(status, werk, warengruppe, search, ohne_klass)
        df_all = get_materials()
        df = apply_filters(df_all, filters)
        counter = f"{df.height} / {df_all.height} Datensätze"
        return df.to_dicts(), counter

    # ---------------------------------------------------------------
    # 5) Steuerelemente  ->  aktive/inaktive KPI-Kacheln   (CLIENTSEITIG)
    #
    #    Reine Darstellung, also im Browser statt auf dem Server: die
    #    Kacheln schalten um, sobald der Klick-Callback zurück ist, ohne
    #    eine weitere Server-Runde und ohne auf das Neurendern der
    #    DataTable zu warten. Die Regel (welcher Filter zu welcher Kachel
    #    gehört) reicht `store-kpi-filters` aus kpi/kpi_rules.py herein.
    #    Implementierung: assets/kpi_highlight.js
    # ---------------------------------------------------------------
    app.clientside_callback(
        ClientsideFunction(namespace="kpi", function_name="highlight"),
        Output({"type": "kpi-tile", "kpi": ALL}, "className"),
        Input(IDS.F_STATUS, "value"),
        Input(IDS.F_OHNE_KLASS, "value"),
        State(IDS.STORE_KPI_FILTERS, "data"),
    )
