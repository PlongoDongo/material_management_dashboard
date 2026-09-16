"""Tests of the clientside column selection (assets/column_menu.js).

As with the KPI highlight, the logic runs in the browser; here it is executed
through node with minimal `window`/`document` stubs. The two pure functions are
checked:
  - applyVisibility(values, options) -> columns to hide
  - selectAll(nAll, nNone, options)  -> new value of the checklist
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from data.schema import COLUMNS, FIXED_COLUMNS

ASSET = Path(__file__).resolve().parents[1] / "assets" / "column_menu.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_TOGGLEABLE = [c for c in COLUMNS if c not in FIXED_COLUMNS]
_OPTIONS = [{"label": c, "value": c} for c in _TOGGLEABLE]

_HARNESS = """
global.window = {};
global.document = { addEventListener: function () {} };
%(source)s
window.dash_clientside.callback_context = { triggered: %(triggered)s };
console.log(JSON.stringify(
    window.dash_clientside.cols.%(fn)s.apply(null, %(args)s)));
"""


def _run(fn: str, args: list, triggered: list[dict] | None = None) -> list[str]:
    script = _HARNESS % {
        "source": ASSET.read_text(encoding="utf-8"),
        "triggered": json.dumps(triggered or []),
        "fn": fn,
        "args": json.dumps(args),
    }
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# -- applyVisibility --------------------------------------------------------
def test_all_checked_hides_nothing() -> None:
    assert _run("applyVisibility", [list(_TOGGLEABLE), _OPTIONS]) == []


def test_none_checked_hides_all_toggleable() -> None:
    assert sorted(_run("applyVisibility", [[], _OPTIONS])) == sorted(_TOGGLEABLE)


def test_partial_selection_hides_complement() -> None:
    keep = _TOGGLEABLE[:2]
    hidden = _run("applyVisibility", [keep, _OPTIONS])
    assert sorted(hidden) == sorted(_TOGGLEABLE[2:])


def test_fixed_columns_never_hidden() -> None:
    """The fixed columns do not appear in the options at all ->
    so they can never be hidden, no matter what is ticked."""
    hidden = _run("applyVisibility", [[], _OPTIONS])
    for c in FIXED_COLUMNS:
        assert c not in hidden


# -- selectAll --------------------------------------------------------------
def test_select_all_returns_every_toggleable() -> None:
    out = _run("selectAll", [1, 0, _OPTIONS],
               triggered=[{"prop_id": "columns-all.n_clicks"}])
    assert sorted(out) == sorted(_TOGGLEABLE)


def test_select_none_returns_empty() -> None:
    out = _run("selectAll", [0, 1, _OPTIONS],
               triggered=[{"prop_id": "columns-none.n_clicks"}])
    assert out == []
