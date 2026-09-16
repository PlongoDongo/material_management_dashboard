"""
Data access layer of the dashboard.

This file is the ONLY place that knows where the data comes from. The rest of
the dashboard calls `get_materials()` and gets back a Polars DataFrame -- just
like before. That is why moving from Neo4j to the API layer was a change to
this single file (plus dropping `data/neo4j.py`).

    before:  get_materials() -> Cypher against Neo4j -> DataFrame
    now:     get_materials() -> HTTP to the API layer -> DataFrame

What the dashboard therefore no longer needs:
  * the neo4j driver and its credentials
  * knowledge of the graph model (Cypher)
  * an opinion of its own about what "stock value" means

TWO NAMESPACES, ONE BOUNDARY
============================
The API contract and the column names of the table are NOT the same thing. The
API speaks English (`material_number`, `plant_name`, `stock_value`); the
dashboard labels its columns in German, like the user interface. The
translation happens at this boundary -- see `_API_TO_UI`.

That is deliberate and not a stopgap: the API belongs to another team and is
free to rename its fields without breaking the dashboard. What changes then is
one line here. All that matters is that the translation sits VISIBLY in one
place instead of being scattered across the code.
"""
from __future__ import annotations

import logging
import os
import time

import polars as pl

from auth import access_token, user_roles
from data.api_client import (
    DataProductClient,
    DataProductError,
    NotAuthenticatedError,
    NotAuthorisedError,
)
from data.schema import COLUMN_LABELS, COLUMNS  # noqa: F401  (re-export)

log = logging.getLogger(__name__)

# Which data product in which version. Deliberately hard-wired and not
# "latest": a version change should show up in the git diff and be tested, not
# happen silently because the API rolled out a new major.
PRODUCT = "material-overview"
VERSION = "v3"

# How many rows ONE request fetches. 50,000 is the maximum of
# ProductParams.limit -- the API does not accept more.
PAGE_SIZE = 50_000

# Upper bound across all pages together. The dashboard keeps the complete data
# set in memory, so loading stops here instead of blowing up the worker. If the
# bound is reached, that MUST be noticed (see load_materials): a table that
# looks complete but holds incomplete data is worse than an error message, and
# the KPI tiles do not count the missing rows either.
MAX_ROWS = 500_000

# How long a once-fetched snapshot stays valid inside the dashboard process.
# The server caches as well (cache_ttl of the data product); this cache here
# saves the HTTP round trip on every callback.
CACHE_TTL_SECONDS = int(os.getenv("DATA_CACHE_TTL", "60"))

# API field -> dashboard column. Only what is listed here ends up in the table.
# API fields the dashboard does not need (werk_id, preis) are deliberately
# missing -- new API fields therefore never break the dashboard.
_API_TO_UI: dict[str, str] = {
    "material_number": "material_number",
    "description": "description",
    "material_group": "material_group",
    "plant_name": "plant",
    "status": "status",
    "stock": "stock",
    "stock_value": "stock_value",
    "changed_on": "changed_on",
}

# ONE client per process. It keeps the connection pool open; an
# `httpx.get(...)` per callback would reconnect every time.
_client = DataProductClient()

# A cache container instead of a bare module global, so the function below does
# not have to rebind the name via `global`.
#
# IMPORTANT: the cache is PROCESS-WIDE, but the dashboard serves many users.
# That is why it lives under a key derived from the user's roles. Without it, a
# user without permission would be served the snapshot a permitted colleague
# fetched moments earlier -- the API's 403 would never be raised, because no
# request would run at all. Roles and not user name as the key: everyone with
# the same rights sees the same data, so the hit rate stays high, and only what
# has to be separated is separated.
_CACHE: dict[tuple[str, ...], dict[str, object]] = {}


def _cache_slot() -> dict[str, object]:
    """The cache bucket for the current user's rights."""
    return _CACHE.setdefault(tuple(sorted(user_roles())), {})


def _rows_to_frame(rows: list[dict]) -> pl.DataFrame:
    """API rows -> DataFrame with the dashboard columns.

    Pure and without HTTP: takes rows in, returns a DataFrame. That makes it
    testable without a running API (see tests/test_repository.py).
    """
    if not rows:
        # Empty, but SCHEMA-CORRECT frame. Without a schema the table would
        # crash with "column not found" on the first empty result.
        return pl.DataFrame(
            schema={c: (pl.Int64 if c in ("stock",) else
                        pl.Float64 if c == "stock_value" else pl.Utf8)
                    for c in COLUMNS}
        )

    frame = pl.DataFrame(rows)
    # Take over only known fields and rename them to the UI names.
    known = {api: ui for api, ui in _API_TO_UI.items() if api in frame.columns}
    frame = frame.select(list(known)).rename(known)

    # Add missing columns: if the API does not (yet) deliver a field, the table
    # should still render instead of blowing up.
    for column in COLUMNS:
        if column not in frame.columns:
            log.warning("Data product %s/%s delivers no field for column '%s'.",
                        PRODUCT, VERSION, column)
            frame = frame.with_columns(pl.lit(None).alias(column))

    return frame.select(COLUMNS).with_columns(
        pl.col("stock").cast(pl.Int64, strict=False)
    )


class RowCountChanged(DataProductError):
    """The number of rows changed between two pages.

    The pages then no longer fit together -- rows can show up twice or go
    missing. Inherits from DataProductError so that the fallback path in
    `get_materials` applies if the second attempt fails as well.
    """


def _all_pages(token: str | None) -> tuple[list[dict], dict]:
    """Fetches the data product page by page until the data set is complete.

    The dashboard ALWAYS needs every row: the KPI tiles compute over the entire
    data set, the filter dropdowns show the distinct values of all rows, and the
    search runs in the browser over the complete data set. With only one page
    all three would be silently wrong -- entries would be missing without
    anything reporting it.

    On the server side this is cheap: `material-overview` loads the whole data set
    anyway and only cuts out the window afterwards, and for such products
    limit/offset do NOT enter the cache key. Page two and all further pages
    therefore come from the same cache entry, without a new query -- as long as
    the data product's `cache_ttl` is greater than 0.

    Loading stops as soon as a page is shorter than PAGE_SIZE: the API then has
    nothing left, whatever `total_count` claims. Without that condition an API
    that delivers less than it reports would loop here forever.
    """
    rows: list[dict] = []
    meta: dict = {}
    total: int | None = None

    while True:
        page, meta = _client.fetch(PRODUCT, VERSION, token=token,
                                   limit=PAGE_SIZE, offset=len(rows))
        if total is not None and meta.get("total_count") != total:
            raise RowCountChanged(
                f"total_count changed from {total} to {meta.get('total_count')}"
            )
        total = meta.get("total_count")
        rows.extend(page)

        if (len(page) < PAGE_SIZE or total is None
                or len(rows) >= total or len(rows) >= MAX_ROWS):
            return rows, meta


def load_materials() -> pl.DataFrame:
    """Fetches the data product from the API layer and shapes it for the table."""
    token = access_token()
    try:
        rows, meta = _all_pages(token)
    except RowCountChanged as shifted:
        # Someone wrote while we were loading. Exactly one new attempt; if that
        # fails too, the fallback path in get_materials applies.
        log.warning("Data set changed while loading (%s) -- trying again.", shifted)
        rows, meta = _all_pages(token)

    log.info("Snapshot %s | source %s | %s of %s rows | cache %s",
             meta.get("generated_at"), meta.get("source"),
             len(rows), meta.get("total_count"), meta.get("cache"))

    total = meta.get("total_count") or len(rows)
    if total > len(rows):
        # Not just logging: nobody sees logged errors in production, and the
        # number in the management meeting would then be wrong. The UI shows
        # the hint next to the row counter (see tabs/data_overview.py).
        log.error("Data product truncated: %s of %s rows loaded (upper bound %s). "
                  "KPI tiles and counters are incomplete.",
                  len(rows), total, MAX_ROWS)
        _cache_slot()["truncated"] = (len(rows), total)
    else:
        _cache_slot().pop("truncated", None)

    if meta.get("deprecated"):
        log.warning("Data product %s/%s is deprecated (sunset %s) -- please migrate.",
                    PRODUCT, VERSION, meta.get("sunset"))
    return _rows_to_frame(rows)


def get_materials(*, force_reload: bool = False) -> pl.DataFrame:
    """Cached access. This is the function the rest of the dashboard uses.

    Behaviour when the API is down: if there is still an (expired) snapshot in
    the cache, it keeps being served and a warning is logged -- a dashboard
    showing briefly outdated numbers is better than one that is empty. If there
    is none, the error is passed on instead of silently showing an empty table.
    """
    slot = _cache_slot()
    fresh_until = float(slot.get("expires_at", 0))  # type: ignore[arg-type]
    if not force_reload and slot.get("frame") is not None and time.monotonic() < fresh_until:
        return slot["frame"]  # type: ignore[return-value]

    try:
        frame = load_materials()
    except (NotAuthenticatedError, NotAuthorisedError):
        # Do NOT answer permission errors from the cache: the old snapshot
        # comes from a session that was allowed to see the data. Passing it on
        # would be exactly the gap the role key above prevents.
        raise
    except DataProductError as exc:
        if slot.get("frame") is not None:
            log.warning("API unreachable (%s) -- serving the last snapshot.", exc)
            return slot["frame"]  # type: ignore[return-value]
        log.error("API unreachable and no snapshot in the cache: %s", exc)
        raise

    slot["frame"] = frame
    slot["expires_at"] = time.monotonic() + CACHE_TTL_SECONDS
    return frame


def invalidate() -> None:
    """Call after a successful write action -- the next access fetches anew.

        response = _client.post_mapping(...)     # later: "Apply data mappings" tab
        repository.invalidate()

    On a write the API layer clears its own cache (the `invalidates(...)`
    dependency on the route). The dashboard learns nothing about that and would
    otherwise show the old snapshot for up to CACHE_TTL_SECONDS: the user
    creates a mapping, switches to the overview and does not see their own
    change -- exactly the impression that saving has failed.

    ALL role buckets are cleared, not just one's own: the change affects
    everyone who sees the data.

    Limitation: if Dash runs with several worker processes, this only clears
    the cache of the process that handled the click. The others show the old
    snapshot until the TTL expires.
    """
    _CACHE.clear()


def truncation() -> tuple[int, int] | None:
    """(loaded, total) if the last fetch was truncated -- otherwise None.

    The UI reads this to mark the row counter. A table that looks complete and
    is not is the most expensive class of error.
    """
    return _cache_slot().get("truncated")  # type: ignore[return-value]


def distinct_values(column: str) -> list[str]:
    """Unique, sorted values of a column -- for the filter dropdowns."""
    values = (
        get_materials()
        .select(pl.col(column))
        .drop_nulls()
        .to_series()
        .to_list()
    )
    return sorted({v for v in values if v not in (None, "")})
