"""Tests für die clientseitige Kachel-Hervorhebung (assets/kpi_highlight.js).

Die Hervorhebung lief früher in Python und war hier direkt testbar. Seit sie
aus Latenzgründen im Browser läuft, wird die Funktion über node ausgeführt --
mit einem minimalen `window`-Stub statt eines echten Browsers. Die Regeln
(KPI-ID -> Filter) kommen dabei aus kpi/kpi_rules.py, genau wie zur Laufzeit
über den Store.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kpi.kpi_rules import kpi_filter_map

ASSET = Path(__file__).resolve().parents[1] / "assets" / "kpi_highlight.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node nicht installiert"
)

_HARNESS = """
global.window = {};
var KPIS = %(kpis)s, STATUS = %(status)s, OHNE = %(ohne)s, MAP = %(map)s;
%(source)s
window.dash_clientside.no_update = "__no_update__";
window.dash_clientside.callback_context = {
    outputs_list: KPIS.map(function (k) {
        return { id: { type: "kpi-tile", kpi: k }, property: "className" };
    }),
};
console.log(JSON.stringify(
    window.dash_clientside.kpi.highlight(STATUS, OHNE, MAP)));
"""


def highlight(status, ohne_klass, kpi_ids=None):
    """Ruft die JS-Funktion auf und gibt die classNames zurück."""
    kpi_ids = kpi_ids or list(kpi_filter_map())
    script = _HARNESS % {
        "kpis": json.dumps(kpi_ids),
        "status": json.dumps(status),
        "ohne": json.dumps(ohne_klass),
        "map": json.dumps(kpi_filter_map()),
        "source": ASSET.read_text(encoding="utf-8"),
    }
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _state(classes):
    """['kpi-tile kpi-tile--active', ...] -> ['active', 'muted', ...]"""
    return [c.replace("kpi-tile", "").replace("--", "").strip() or "normal"
            for c in classes]


# --------------------------------------------------------------------------
def test_active_tile_is_marked_and_rest_is_muted():
    out = _state(highlight(["Obsolet"], []))
    ids = list(kpi_filter_map())
    assert out[ids.index("obsolet")] == "active"
    assert out.count("active") == 1
    assert out.count("muted") == len(ids) - 1


def test_ohne_klassifizierung_tile():
    out = _state(highlight([], ["on"]))
    ids = list(kpi_filter_map())
    assert out[ids.index("ohne_klassifizierung")] == "active"
    assert out.count("active") == 1


def test_no_filter_leaves_all_tiles_normal():
    """Ausgangszustand: nichts hervorgehoben UND nichts ausgegraut."""
    assert set(_state(highlight([], []))) == {"normal"}
    assert set(_state(highlight(None, None))) == {"normal"}


def test_foreign_filter_leaves_all_tiles_normal():
    """Ein Status, den keine Kachel setzt (z. B. aus der Sidebar)."""
    assert set(_state(highlight(["Aktiv", "Obsolet"], []))) == {"normal"}


def test_status_order_does_not_matter():
    """Mengenvergleich, nicht Listenvergleich."""
    assert _state(highlight(["Aktiv"], [])) == _state(highlight(["Aktiv"], []))


def test_status_match_requires_matching_flag():
    """Status passt, aber das Klassifizierungs-Flag steht quer -> nicht aktiv."""
    assert set(_state(highlight(["Aktiv"], ["on"]))) == {"normal"}


def test_matches_python_rule_for_every_tile():
    """JS-Vergleich und _kpi_is_active (Python) müssen übereinstimmen."""
    from callbacks.filter_callbacks import _kpi_is_active

    for kpi_id, flt in kpi_filter_map().items():
        status = list(flt.get("status", []))
        ohne = ["on"] if flt.get("ohne_klass") else []
        js = _state(highlight(status, ohne))
        for i, other in enumerate(kpi_filter_map()):
            expected = _kpi_is_active(other, status, ohne)
            assert (js[i] == "active") is expected, (kpi_id, other, js)
