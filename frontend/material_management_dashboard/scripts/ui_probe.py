"""
Frontend probe: starts the app headless, measures the layout zones and takes a
screenshot. A dev tool (not part of the app).

    pip install -r requirements-dev.txt
    python -m playwright install chromium        # once
    python scripts/ui_probe.py [WIDTH HEIGHT]    # default 1600 1000

Screenshots end up in test-artifacts/ (excluded via .gitignore). Handy for
checking CSS and layout changes without driving the browser by hand.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path("test-artifacts")
OUT.mkdir(exist_ok=True)
PORT = 8062
URL = f"http://127.0.0.1:{PORT}/"

# Elements whose geometry is of interest when debugging the layout.
_PROBE_SELECTORS = [
    ".app-shell", ".app-main", "#content-overview", ".kpi-row", ".table-card",
    ".dash-spreadsheet-container", ".previous-next-container", ".app-footer",
]


def _start_app() -> None:
    import app as a
    a.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def _wait_ready(timeout: float = 25.0) -> None:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)
            return
        except OSError:
            time.sleep(0.4)
    raise RuntimeError("App server not reachable in time")


def _rect(page, selector: str) -> dict | None:
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            const cs = getComputedStyle(el);
            return {x: Math.round(r.x), y: Math.round(r.y),
                    w: Math.round(r.width), h: Math.round(r.height),
                    bottom: Math.round(r.bottom),
                    disp: cs.display, dir: cs.flexDirection, pos: cs.position};
        }""",
        selector,
    )


def main() -> None:
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 1600
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 1000

    threading.Thread(target=_start_app, daemon=True).start()
    _wait_ready()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("#material-table", timeout=10000)
        time.sleep(1.5)  # let the DataTable finish rendering

        print(f"viewport: {w}x{h}")
        for sel in _PROBE_SELECTORS:
            r = _rect(page, sel)
            if r is None:
                print(f"{sel:44s} -> NOT FOUND")
            else:
                print(f"{sel:44s} -> y={r['y']:4d} h={r['h']:4d} "
                      f"bottom={r['bottom']:4d} disp={r['disp']} dir={r['dir']}")

        shot = OUT / f"overview_{w}x{h}.png"
        page.screenshot(path=str(shot))
        print(f"\nScreenshot -> {shot}")
        browser.close()


if __name__ == "__main__":
    main()
