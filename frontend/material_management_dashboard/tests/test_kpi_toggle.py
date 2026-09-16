"""Tests of the toggle behaviour of the KPI tiles.

The callbacks are checked without a running Dash server: `register_filter_callbacks`
is handed a dummy that only collects the functions instead of registering them.
The `callback_context` (ctx.triggered_id / ctx.outputs_list) is set through the
ContextVar that Dash uses internally anyway.
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

# The collected callbacks: function name -> undecorated function
Callbacks = dict[str, Callable]


class _CollectingApp:
    """Replaces `app` and captures the undecorated callback functions."""

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
        # Clientside callbacks have no Python function to test --
        # just remember that they were registered (see tests/test_kpi_highlight_js.py).
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
    """The highlighting must not cost a server round trip any more."""
    assert len(app_stub.clientside) == 1
    func, _ = app_stub.clientside[0]
    assert isinstance(func, ClientsideFunction)
    assert (func.namespace, func.function_name) == ("kpi", "highlight")


def _set_ctx(**kwargs: object) -> None:
    context_value.set(AttributeDict(**kwargs))


def _tile_click(kpi_id: str) -> None:
    """Builds the callback_context of a click on the tile `kpi_id`."""
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
    # The status filter matches, but the classification flag is in the way
    assert not _kpi_is_active("aktiv", ["Aktiv"], ["on"])
    # No filter set -> the "without classification" tile is inactive too
    assert not _kpi_is_active("ohne_klassifizierung", [], [])


# --------------------------------------------------------------------------
# 1) Click on a tile: set vs. clear
# --------------------------------------------------------------------------
def test_click_sets_filter(cb: Callbacks) -> None:
    _tile_click("obsolet")
    assert cb["kpi_click_to_filter"]([1], [], []) == (["Obsolet"], [])


def test_click_on_active_tile_clears_filter(cb: Callbacks) -> None:
    """Core requirement: the same tile again -> filter gone."""
    _tile_click("obsolet")
    assert cb["kpi_click_to_filter"]([2], ["Obsolet"], []) == ([], [])


def test_click_switches_between_tiles(cb: Callbacks) -> None:
    """A different tile -> switch over, do not clear."""
    _tile_click("gesperrt")
    assert cb["kpi_click_to_filter"]([1], ["Obsolet"], []) == (["Gesperrt"], [])


def test_the_unclassified_tile_toggles(cb: Callbacks) -> None:
    _tile_click("ohne_klassifizierung")
    assert cb["kpi_click_to_filter"]([1], [], []) == ([], ["on"])
    _tile_click("ohne_klassifizierung")
    assert cb["kpi_click_to_filter"]([2], [], ["on"]) == ([], [])


# --------------------------------------------------------------------------
# 1b) Click on empty space
# --------------------------------------------------------------------------
def test_empty_click_clears_active_filter(cb: Callbacks) -> None:
    assert cb["empty_click_clears_kpi_filter"](123, ["Aktiv"], []) == ([], [])
    assert cb["empty_click_clears_kpi_filter"](123, [], ["on"]) == ([], [])


def test_empty_click_is_noop_without_filter(cb: Callbacks) -> None:
    """Without an active filter, no superfluous rerender."""
    assert cb["empty_click_clears_kpi_filter"](123, [], []) == (no_update, no_update)


def test_empty_click_keeps_sidebar_filters(cb: Callbacks) -> None:
    """Search/plant/material group are not part of the output -> stay untouched."""
    outs = cb["empty_click_clears_kpi_filter"](123, ["Aktiv"], [])
    assert len(outs) == 2  # only status + ohne_klass


# --------------------------------------------------------------------------
# 3/4) Filter state -- one normalization for the store AND the table
# --------------------------------------------------------------------------
def test_filter_state_normalizes(cb: Callbacks) -> None:
    assert filter_state(None, None, None, None, None) == {
        "status": [], "plant": [], "material_group": [], "search": "", "ohne_klass": False,
    }
    assert filter_state(["Aktiv"], [], [], "abc", ["on"])["ohne_klass"] is True


def test_store_and_table_see_the_same_filter(cb: Callbacks) -> None:
    """Both callbacks derive the same state from the same inputs."""
    args = (["Aktiv"], ["Werk Köln"], [], "ring", ["on"])
    store = cb["build_filter_state"](*args)
    # render_table returns data, but internally has to build the same filter
    assert store == filter_state(*args)
