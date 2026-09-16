"""Tests of the clientside tile highlighting (assets/kpi_highlight.js).

The highlighting used to run in Python and was directly testable here. Since it
runs in the browser for latency reasons, the function is executed through node
-- with a minimal `window` stub instead of a real browser. The rules
(KPI id -> filter) come from kpi/kpi_rules.py, exactly as they do at runtime
through the store.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from callbacks.filter_callbacks import _kpi_is_active
from kpi.kpi_rules import kpi_filter_map

ASSET = Path(__file__).resolve().parents[1] / "assets" / "kpi_highlight.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_HARNESS = """
global.window = {};
var KPIS = %(kpis)s, STATUS = %(status)s, UNCLASSIFIED = %(unclassified)s, MAP = %(map)s;
%(source)s
window.dash_clientside.no_update = "__no_update__";
window.dash_clientside.callback_context = {
    outputs_list: KPIS.map(function (k) {
        return { id: { type: "kpi-tile", kpi: k }, property: "className" };
    }),
};
console.log(JSON.stringify(
    window.dash_clientside.kpi.highlight(STATUS, UNCLASSIFIED, MAP)));
"""


def highlight(
    status: list[str] | None,
    ohne_klass: list[str] | None,
    kpi_ids: list[str] | None = None,
) -> list[str]:
    """Calls the JS function and returns the classNames."""
    kpi_ids = kpi_ids or list(kpi_filter_map())
    script = _HARNESS % {
        "kpis": json.dumps(kpi_ids),
        "status": json.dumps(status),
        "unclassified": json.dumps(ohne_klass),
        "map": json.dumps(kpi_filter_map()),
        "source": ASSET.read_text(encoding="utf-8"),
    }
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _state(classes: list[str]) -> list[str]:
    """['kpi-tile kpi-tile--active', ...] -> ['active', 'muted', ...]"""
    return [c.replace("kpi-tile", "").replace("--", "").strip() or "normal"
            for c in classes]


# --------------------------------------------------------------------------
def test_active_tile_is_marked_and_rest_is_muted() -> None:
    out = _state(highlight(["Obsolet"], []))
    ids = list(kpi_filter_map())
    assert out[ids.index("obsolet")] == "active"
    assert out.count("active") == 1
    assert out.count("muted") == len(ids) - 1


def test_the_unclassified_tile_is_marked() -> None:
    out = _state(highlight([], ["on"]))
    ids = list(kpi_filter_map())
    assert out[ids.index("ohne_klassifizierung")] == "active"
    assert out.count("active") == 1


def test_no_filter_leaves_all_tiles_normal() -> None:
    """Initial state: nothing highlighted AND nothing greyed out."""
    assert set(_state(highlight([], []))) == {"normal"}
    assert set(_state(highlight(None, None))) == {"normal"}


def test_foreign_filter_leaves_all_tiles_normal() -> None:
    """A status that no tile sets (e.g. one coming from the sidebar)."""
    assert set(_state(highlight(["Aktiv", "Obsolet"], []))) == {"normal"}


def test_status_order_does_not_matter() -> None:
    """Set comparison, not list comparison."""
    assert _state(highlight(["Aktiv"], [])) == _state(highlight(["Aktiv"], []))


def test_status_match_requires_matching_flag() -> None:
    """The status matches, but the classification flag is in the way -> not active."""
    assert set(_state(highlight(["Aktiv"], ["on"]))) == {"normal"}


def test_matches_python_rule_for_every_tile() -> None:
    """The JS comparison and _kpi_is_active (Python) have to agree."""
    for kpi_id, flt in kpi_filter_map().items():
        status = list(flt.get("status", []))
        unclassified = ["on"] if flt.get("ohne_klass") else []
        js = _state(highlight(status, unclassified))
        for i, other in enumerate(kpi_filter_map()):
            expected = _kpi_is_active(other, status, unclassified)
            assert (js[i] == "active") is expected, (kpi_id, other, js)
