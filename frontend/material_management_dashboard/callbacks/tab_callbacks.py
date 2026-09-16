"""
Tab callbacks: switching between the three footer tabs.

Approach: all three tab contents stay in the DOM permanently; we only toggle
their visibility via the `display` style. That is the most robust way, because
it keeps every callback target (above all the table) in existence at all times,
so the filter state takes effect immediately when switching back.

Persistence of the filters across tabs
--------------------------------------
Because the header, both sidebars AND all tab contents hang off the same
top-level layout, which is never re-rendered, neither the filter controls nor
the `store-filters` state are lost when switching tabs. That is precisely the
reason why we do NOT use Plotly Pages (URL routing) here -- with those, the page
content would be swapped out on every switch (see README).
"""
from __future__ import annotations

from dash import Dash, Input, Output, ctx

from config import IDS, TABS

# Visible = flex: .tab-content is a column flex container (fill the height,
# table anchored at the bottom against the footer). An inline "block" would
# override the CSS layout, hence "flex" here on purpose.
_VISIBLE = {"display": "flex"}
_HIDDEN = {"display": "none"}

# Mapping tab id -> content container id
_TAB_CONTENT = {
    IDS.TAB_OVERVIEW: IDS.CONTENT_OVERVIEW,
    IDS.TAB_MANAGE: IDS.CONTENT_MANAGE,
    IDS.TAB_MAPPINGS: IDS.CONTENT_MAPPINGS,
}


def register_tab_callbacks(app: Dash) -> None:

    @app.callback(
        Output(IDS.STORE_ACTIVE_TAB, "data"),
        Output(IDS.CONTENT_OVERVIEW, "style"),
        Output(IDS.CONTENT_MANAGE, "style"),
        Output(IDS.CONTENT_MAPPINGS, "style"),
        Output(IDS.TAB_OVERVIEW, "className"),
        Output(IDS.TAB_MANAGE, "className"),
        Output(IDS.TAB_MAPPINGS, "className"),
        Input(IDS.TAB_OVERVIEW, "n_clicks"),
        Input(IDS.TAB_MANAGE, "n_clicks"),
        Input(IDS.TAB_MAPPINGS, "n_clicks"),
        prevent_initial_call=True,
    )
    def switch_tab(
        _a: int | None, _b: int | None, _c: int | None
    ) -> tuple[str, dict, dict, dict, str, str, str]:
        active = ctx.triggered_id or IDS.TAB_OVERVIEW

        styles = [
            _VISIBLE if _TAB_CONTENT[IDS.TAB_OVERVIEW] == _TAB_CONTENT[active] else _HIDDEN,
            _VISIBLE if _TAB_CONTENT[IDS.TAB_MANAGE] == _TAB_CONTENT[active] else _HIDDEN,
            _VISIBLE if _TAB_CONTENT[IDS.TAB_MAPPINGS] == _TAB_CONTENT[active] else _HIDDEN,
        ]
        classes = [
            "footer-tab" + (" active" if tab_id == active else "")
            for tab_id, _ in TABS
        ]
        return active, *styles, *classes
