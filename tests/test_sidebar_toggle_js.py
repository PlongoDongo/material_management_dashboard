"""Tests für das clientseitige Sidebar-Toggle (assets/sidebar_toggle.js).

Wie bei den anderen clientseitigen Funktionen wird die Logik über node mit
einem minimalen window-Stub ausgeführt. Geprüft wird das Umschalt-Verhalten:
Icon = umschalten, Overlay/Schließen = immer schließen.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSET = Path(__file__).resolve().parents[1] / "assets" / "sidebar_toggle.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node nicht installiert"
)

_HARNESS = """
global.window = {};
%(source)s
window.dash_clientside.callback_context = { triggered: [{ prop_id: %(trigger)s }] };
console.log(JSON.stringify(
    window.dash_clientside.sidebar.%(fn)s.apply(null, %(args)s)));
"""


def _call(fn: str, trigger: str, cls: str) -> list[str]:
    # args: (btn, overlay, close, currentClassName) -- nur cls ist relevant
    script = _HARNESS % {
        "source": ASSET.read_text(encoding="utf-8"),
        "trigger": json.dumps(trigger),
        "fn": fn,
        "args": json.dumps([1, 0, 0, cls]),
    }
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# -- Nav-Sidebar ------------------------------------------------------------
def test_menu_click_opens_when_closed() -> None:
    assert _call("toggleNav", "menu-btn.n_clicks", "sidebar sidebar-nav") == [
        "sidebar sidebar-nav open", "sidebar-overlay open"]


def test_menu_click_closes_when_open() -> None:
    assert _call("toggleNav", "menu-btn.n_clicks", "sidebar sidebar-nav open") == [
        "sidebar sidebar-nav", "sidebar-overlay"]


def test_overlay_click_always_closes() -> None:
    assert _call("toggleNav", "nav-overlay.n_clicks", "sidebar sidebar-nav open") == [
        "sidebar sidebar-nav", "sidebar-overlay"]


def test_close_button_always_closes() -> None:
    assert _call("toggleNav", "nav-close.n_clicks", "sidebar sidebar-nav open") == [
        "sidebar sidebar-nav", "sidebar-overlay"]


# -- Filter-Sidebar ---------------------------------------------------------
def test_filter_icon_opens_when_closed() -> None:
    assert _call("toggleFilter", "filter-btn.n_clicks", "sidebar sidebar-filter") == [
        "sidebar sidebar-filter open", "sidebar-overlay open"]


def test_filter_overlay_closes() -> None:
    assert _call("toggleFilter", "filter-overlay.n_clicks",
                 "sidebar sidebar-filter open") == [
        "sidebar sidebar-filter", "sidebar-overlay"]
