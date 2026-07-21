"""Tests für das Toggle-Verhalten der KPI-Kacheln.

Die Callbacks werden ohne laufenden Dash-Server geprüft: `register_filter_callbacks`
bekommt eine Attrappe untergeschoben, die die Funktionen nur einsammelt statt sie
zu registrieren. Der `callback_context` (ctx.triggered_id / ctx.outputs_list) wird
über die ContextVar gesetzt, die Dash intern ohnehin verwendet.
"""
import pytest
from dash._callback_context import context_value
from dash._utils import AttributeDict

from callbacks.filter_callbacks import _kpi_is_active, register_filter_callbacks


class _CollectingApp:
    """Ersetzt `app` und fängt die undekorierten Callback-Funktionen ab."""

    def __init__(self):
        self.fns = {}

    def callback(self, *_args, **_kwargs):
        def deco(fn):
            self.fns[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture(scope="module")
def cb():
    app = _CollectingApp()
    register_filter_callbacks(app)
    return app.fns


def _set_ctx(**kwargs):
    context_value.set(AttributeDict(**kwargs))


def _tile_click(kpi_id):
    """Baut den callback_context eines Klicks auf die Kachel `kpi_id`."""
    _set_ctx(triggered_inputs=[
        {"prop_id": '{"kpi":"%s","type":"kpi-tile"}.n_clicks' % kpi_id}
    ])


# --------------------------------------------------------------------------
# _kpi_is_active
# --------------------------------------------------------------------------
def test_active_when_filter_matches_tile():
    assert _kpi_is_active("aktiv", ["Aktiv"], [])
    assert _kpi_is_active("ohne_klassifizierung", [], ["on"])


def test_inactive_on_empty_or_foreign_filter():
    assert not _kpi_is_active("aktiv", [], [])
    assert not _kpi_is_active("aktiv", ["Obsolet"], [])
    # Statusfilter passt, aber das Klassifizierungs-Flag steht quer
    assert not _kpi_is_active("aktiv", ["Aktiv"], ["on"])
    # Kein Filter gesetzt -> auch die "ohne Klassifizierung"-Kachel ist inaktiv
    assert not _kpi_is_active("ohne_klassifizierung", [], [])


# --------------------------------------------------------------------------
# 1) Klick auf Kachel: setzen vs. aufheben
# --------------------------------------------------------------------------
def test_click_sets_filter(cb):
    _tile_click("obsolet")
    assert cb["kpi_click_to_filter"]([1], [], []) == (["Obsolet"], [])


def test_click_on_active_tile_clears_filter(cb):
    """Kernanforderung: dieselbe Kachel erneut -> Filter weg."""
    _tile_click("obsolet")
    assert cb["kpi_click_to_filter"]([2], ["Obsolet"], []) == ([], [])


def test_click_switches_between_tiles(cb):
    """Andere Kachel -> umschalten, nicht aufheben."""
    _tile_click("gesperrt")
    assert cb["kpi_click_to_filter"]([1], ["Obsolet"], []) == (["Gesperrt"], [])


def test_ohne_klassifizierung_toggles(cb):
    _tile_click("ohne_klassifizierung")
    assert cb["kpi_click_to_filter"]([1], [], []) == ([], ["on"])
    _tile_click("ohne_klassifizierung")
    assert cb["kpi_click_to_filter"]([2], [], ["on"]) == ([], [])


# --------------------------------------------------------------------------
# 1b) Klick auf leere Fläche
# --------------------------------------------------------------------------
def test_empty_click_clears_active_filter(cb):
    assert cb["empty_click_clears_kpi_filter"](123, ["Aktiv"], []) == ([], [])
    assert cb["empty_click_clears_kpi_filter"](123, [], ["on"]) == ([], [])


def test_empty_click_is_noop_without_filter(cb):
    """Ohne aktiven Filter kein überflüssiger Rerender."""
    from dash import no_update
    assert cb["empty_click_clears_kpi_filter"](123, [], []) == (no_update, no_update)


def test_empty_click_keeps_sidebar_filters(cb):
    """Suche/Werk/Warengruppe sind nicht Teil der Ausgabe -> bleiben unberührt."""
    outs = cb["empty_click_clears_kpi_filter"](123, ["Aktiv"], [])
    assert len(outs) == 2  # nur Status + ohne_klass


# --------------------------------------------------------------------------
# 5) Hervorhebung der aktiven Kachel
# --------------------------------------------------------------------------
def _outputs_list(*kpi_ids):
    return [{"id": {"type": "kpi-tile", "kpi": k}, "property": "className"}
            for k in kpi_ids]


def test_highlight_marks_only_the_matching_tile(cb):
    _set_ctx(outputs_list=_outputs_list("aktiv", "obsolet", "ohne_klassifizierung"))
    out = cb["highlight_active_kpi"]({"status": ["Obsolet"], "ohne_klass": False})
    assert out == ["kpi-tile", "kpi-tile kpi-tile--active", "kpi-tile"]


def test_highlight_none_without_filter(cb):
    _set_ctx(outputs_list=_outputs_list("aktiv", "obsolet"))
    assert cb["highlight_active_kpi"]({}) == ["kpi-tile", "kpi-tile"]
    _set_ctx(outputs_list=_outputs_list("aktiv", "obsolet"))
    assert cb["highlight_active_kpi"](None) == ["kpi-tile", "kpi-tile"]
