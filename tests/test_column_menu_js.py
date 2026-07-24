"""Tests für die clientseitige Spaltenauswahl (assets/column_menu.js).

Wie beim KPI-Highlight läuft die Logik im Browser; hier wird sie über node
mit minimalen `window`/`document`-Stubs ausgeführt. Geprüft werden die beiden
reinen Funktionen:
  - applyVisibility(values, options) -> auszublendende Spalten
  - selectAll(nAll, nNone, options)  -> neuer Wert der Checkliste
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from config import FIXED_COLUMNS
from data.repository import COLUMNS

ASSET = Path(__file__).resolve().parents[1] / "assets" / "column_menu.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node nicht installiert"
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


def _run(fn, args, triggered=None):
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
def test_all_checked_hides_nothing():
    assert _run("applyVisibility", [list(_TOGGLEABLE), _OPTIONS]) == []


def test_none_checked_hides_all_toggleable():
    assert sorted(_run("applyVisibility", [[], _OPTIONS])) == sorted(_TOGGLEABLE)


def test_partial_selection_hides_complement():
    keep = _TOGGLEABLE[:2]
    hidden = _run("applyVisibility", [keep, _OPTIONS])
    assert sorted(hidden) == sorted(_TOGGLEABLE[2:])


def test_fixed_columns_never_hidden():
    """Die fixierten Spalten tauchen gar nicht in den Optionen auf ->
    können also nie ausgeblendet werden, egal was angehakt ist."""
    hidden = _run("applyVisibility", [[], _OPTIONS])
    for c in FIXED_COLUMNS:
        assert c not in hidden


# -- selectAll --------------------------------------------------------------
def test_select_all_returns_every_toggleable():
    out = _run("selectAll", [1, 0, _OPTIONS],
               triggered=[{"prop_id": "columns-all.n_clicks"}])
    assert sorted(out) == sorted(_TOGGLEABLE)


def test_select_none_returns_empty():
    out = _run("selectAll", [0, 1, _OPTIONS],
               triggered=[{"prop_id": "columns-none.n_clicks"}])
    assert out == []
