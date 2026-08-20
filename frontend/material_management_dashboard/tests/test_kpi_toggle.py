"""Tests für das Toggle-Verhalten der KPI-Kacheln.

Die Callbacks werden ohne laufenden Dash-Server geprüft: `register_filter_callbacks`
bekommt eine Attrappe untergeschoben, die die Funktionen nur einsammelt statt sie
zu registrieren. Der `callback_context` (ctx.triggered_id / ctx.outputs_list) wird
über die ContextVar gesetzt, die Dash intern ohnehin verwendet.
"""
from collections.abc import Callable

import pytest
from dash import ClientsideFunction, no_update
from dash._callback_context import context_value
from dash._utils import AttributeDict

from callbacks.filter_callbacks import (
    _kpi_is_active,
    filter_state,
    register_filter_callbacks,
)

# Die eingesammelten Callbacks: Funktionsname -> undekorierte Funktion
Callbacks = dict[str, Callable]


class _CollectingApp:
    """Ersetzt `app` und fängt die undekorierten Callback-Funktionen ab."""

    def __init__(self) -> None:
        self.fns: Callbacks = {}
        self.clientside: list[tuple[ClientsideFunction, tuple]] = []

    def callback(self, *_args: object, **_kwargs: object) -> Callable:
        def deco(fn: Callable) -> Callable:
            self.fns[fn.__name__] = fn
            return fn
        return deco

    def clientside_callback(
        self, func: ClientsideFunction, *args: object, **_kwargs: object
    ) -> None:
        # Clientseitige Callbacks haben keine Python-Funktion zum Testen --
        # nur merken, dass sie registriert wurden (s. tests/test_kpi_highlight_js.py).
        self.clientside.append((func, args))


@pytest.fixture(scope="module")
def app_stub() -> _CollectingApp:
    app = _CollectingApp()
    register_filter_callbacks(app)
    return app


@pytest.fixture(scope="module")
def cb(app_stub: _CollectingApp) -> Callbacks:
    return app_stub.fns


def test_highlight_is_registered_clientside(app_stub: _CollectingApp) -> None:
    """Die Hervorhebung darf keine Server-Runde mehr kosten."""
    assert len(app_stub.clientside) == 1
    func, _ = app_stub.clientside[0]
    assert isinstance(func, ClientsideFunction)
    assert (func.namespace, func.function_name) == ("kpi", "highlight")


def _set_ctx(**kwargs: object) -> None:
    context_value.set(AttributeDict(**kwargs))


def _tile_click(kpi_id: str) -> None:
    """Baut den callback_context eines Klicks auf die Kachel `kpi_id`."""
    _set_ctx(triggered_inputs=[
        {"prop_id": '{"kpi":"%s","type":"kpi-tile"}.n_clicks' % kpi_id}
    ])


# --------------------------------------------------------------------------
# _kpi_is_active
# --------------------------------------------------------------------------
def test_active_when_filter_matches_tile() -> None:
    assert _kpi_is_active("aktiv", ["Aktiv"], [])
    assert _kpi_is_active("ohne_klassifizierung", [], ["on"])


def test_inactive_on_empty_or_foreign_filter() -> None:
    assert not _kpi_is_active("aktiv", [], [])
    assert not _kpi_is_active("aktiv", ["Obsolet"], [])
    # Statusfilter passt, aber das Klassifizierungs-Flag steht quer
    assert not _kpi_is_active("aktiv", ["Aktiv"], ["on"])
    # Kein Filter gesetzt -> auch die "ohne Klassifizierung"-Kachel ist inaktiv
    assert not _kpi_is_active("ohne_klassifizierung", [], [])


# --------------------------------------------------------------------------
# 1) Klick auf Kachel: setzen vs. aufheben
# --------------------------------------------------------------------------
def test_click_sets_filter(cb: Callbacks) -> None:
    _tile_click("obsolet")
    assert cb["kpi_click_to_filter"]([1], [], []) == (["Obsolet"], [])


def test_click_on_active_tile_clears_filter(cb: Callbacks) -> None:
    """Kernanforderung: dieselbe Kachel erneut -> Filter weg."""
    _tile_click("obsolet")
    assert cb["kpi_click_to_filter"]([2], ["Obsolet"], []) == ([], [])


def test_click_switches_between_tiles(cb: Callbacks) -> None:
    """Andere Kachel -> umschalten, nicht aufheben."""
    _tile_click("gesperrt")
    assert cb["kpi_click_to_filter"]([1], ["Obsolet"], []) == (["Gesperrt"], [])


def test_ohne_klassifizierung_toggles(cb: Callbacks) -> None:
    _tile_click("ohne_klassifizierung")
    assert cb["kpi_click_to_filter"]([1], [], []) == ([], ["on"])
    _tile_click("ohne_klassifizierung")
    assert cb["kpi_click_to_filter"]([2], [], ["on"]) == ([], [])


# --------------------------------------------------------------------------
# 1b) Klick auf leere Fläche
# --------------------------------------------------------------------------
def test_empty_click_clears_active_filter(cb: Callbacks) -> None:
    assert cb["empty_click_clears_kpi_filter"](123, ["Aktiv"], []) == ([], [])
    assert cb["empty_click_clears_kpi_filter"](123, [], ["on"]) == ([], [])


def test_empty_click_is_noop_without_filter(cb: Callbacks) -> None:
    """Ohne aktiven Filter kein überflüssiger Rerender."""
    assert cb["empty_click_clears_kpi_filter"](123, [], []) == (no_update, no_update)


def test_empty_click_keeps_sidebar_filters(cb: Callbacks) -> None:
    """Suche/Werk/Warengruppe sind nicht Teil der Ausgabe -> bleiben unberührt."""
    outs = cb["empty_click_clears_kpi_filter"](123, ["Aktiv"], [])
    assert len(outs) == 2  # nur Status + ohne_klass


# --------------------------------------------------------------------------
# 3/4) Filterzustand -- eine Normalisierung für Store UND Tabelle
# --------------------------------------------------------------------------
def test_filter_state_normalizes(cb: Callbacks) -> None:
    assert filter_state(None, None, None, None, None) == {
        "status": [], "werk": [], "warengruppe": [], "search": "", "ohne_klass": False,
    }
    assert filter_state(["Aktiv"], [], [], "abc", ["on"])["ohne_klass"] is True


def test_store_and_table_see_the_same_filter(cb: Callbacks) -> None:
    """Beide Callbacks leiten aus denselben Eingaben denselben Zustand ab."""
    args = (["Aktiv"], ["Werk Köln"], [], "ring", ["on"])
    store = cb["build_filter_state"](*args)
    # render_table liefert Daten, muss aber intern denselben Filter bauen
    assert store == filter_state(*args)
