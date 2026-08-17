"""
Spaltenauswahl der Materialtabelle -- rein clientseitig.

Warum clientseitig? Es ist reine Darstellung (welche Spalten die DataTable
zeigt) und braucht keine Server-Daten. So schaltet die Auswahl ohne
Server-Runde um -- konsistent mit der KPI-Hervorhebung (assets/kpi_highlight.js).

Zwei Callbacks:
  1. Checkliste  -> hidden_columns der Tabelle (unsichtbare = nicht angehakte)
  2. "Alle"/"Keine" -> setzt die Checkliste (chained danach in 1.)

Das Öffnen/Schließen des Popovers selbst ist kein Dash-Callback, sondern ein
schlanker Dokument-Listener in assets/column_menu.js (Klick auf den Button
toggelt, Klick daneben schließt).
"""
from __future__ import annotations

from dash import ClientsideFunction, Dash, Input, Output, State

from config import IDS


def register_column_callbacks(app: Dash) -> None:
    # 1) Angehakte Spalten -> hidden_columns (alle nicht angehakten toggelbaren)
    app.clientside_callback(
        ClientsideFunction(namespace="cols", function_name="applyVisibility"),
        Output(IDS.TABLE, "hidden_columns"),
        Input(IDS.COLS_CHECKLIST, "value"),
        State(IDS.COLS_CHECKLIST, "options"),
    )

    # 2) "Alle" / "Keine" -> Checkliste setzen (löst danach Callback 1 aus)
    app.clientside_callback(
        ClientsideFunction(namespace="cols", function_name="selectAll"),
        Output(IDS.COLS_CHECKLIST, "value"),
        Input(IDS.COLS_ALL, "n_clicks"),
        Input(IDS.COLS_NONE, "n_clicks"),
        State(IDS.COLS_CHECKLIST, "options"),
        prevent_initial_call=True,
    )
