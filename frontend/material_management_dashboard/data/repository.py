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

The table columns are named exactly like the fields of the data product (see
data/schema.py), so there is nothing to map here: the frame keeps the columns
listed in COLUMNS and drops the rest. A field the API renames is a column id to
rename in data/schema.py, and `_rows_to_frame` logs a warning until that is done.

NO AUTHENTICATION YET
=====================
The API runs without OIDC at the moment, so no token is sent. Three things come
back when it is switched on: the token on `_client.fetch(...)`, a cache key per
role (otherwise one user is served what another fetched), and re-raising
`NotAuthenticatedError` / `NotAuthorisedError` in `get_materials` instead of
answering them from the cache.
"""
from __future__ import annotations

import logging
import os
import time

import polars as pl

from data.api_client import DataProductClient, DataProductError
from data.schema import COLUMNS

log = logging.getLogger(__name__)

# Which data product in which version. Deliberately hard-wired and not
# "latest": a version change should show up in the git diff and be tested, not
# happen silently because the API rolled out a new major.
PRODUCT = "material-overview"
VERSION = "v3"

# How many rows ONE request fetches. 50,000 is the maximum of
# ProductParams.limit -- the API does not accept more.
PAGE_SIZE = 50_000

# Only a log threshold, not a limit: everything is loaded either way. About
# 200,000 materials are expected, and the dashboard holds them all in memory,
# so we want to hear about it long before that becomes a problem.
EXPECTED_MAX_ROWS = 300_000

# How long a once-fetched snapshot stays valid inside the dashboard process.
# The server caches as well (cache_ttl of the data product); this cache here
# saves the HTTP round trip on every callback.
CACHE_TTL_SECONDS = int(os.getenv("DATA_CACHE_TTL", "60"))

# ONE client per process. It keeps the connection pool open; an
# `httpx.get(...)` per callback would reconnect every time.
_client = DataProductClient()

# A cache container instead of a bare module global, so the functions below do
# not have to rebind the name via `global`.
_CACHE: dict[str, object] = {}


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

    # Add missing columns: if the API does not (yet) deliver a field, the table
    # should still render instead of blowing up. Fields the dashboard does not
    # show (plant_id, price) are dropped by the `select` below, so a new API
    # field never breaks the table either.
    for column in COLUMNS:
        if column not in frame.columns:
            log.warning("Data product %s/%s delivers no field for column '%s'.",
                        PRODUCT, VERSION, column)
            frame = frame.with_columns(pl.lit(None).alias(column))

    return frame.select(COLUMNS).with_columns(
        pl.col("stock").cast(pl.Int64, strict=False)
    )


def _all_pages() -> tuple[list[dict], dict]:
    """Fetches the data product page by page until the data set is complete.

    The dashboard ALWAYS needs every row: the KPI tiles compute over the entire
    data set, the filter dropdowns show the distinct values of all rows, and the
    search runs in the browser over the complete data set. With only one page
    all three would be silently wrong -- entries would be missing without
    anything reporting it.

    On the server side this is cheap: `material-overview` loads the whole data
    set anyway and only cuts out the window afterwards, and for such products
    limit/offset do NOT enter the cache key. Page two and all further pages
    therefore come from the same cache entry, without a new query -- as long as
    the data product's `cache_ttl` is greater than 0.

    Loading stops as soon as a page is shorter than PAGE_SIZE: the API then has
    nothing left, whatever `total_count` claims. Without that condition an API
    that delivers less than it reports would loop here forever.

    Two attempts: a changing `total_count` means somebody wrote while we were
    loading, and pages from two different states do not fit together -- rows
    can show up twice or go missing.
    """
    for _ in range(2):
        rows: list[dict] = []
        total: int | None = None

        while True:
            page, meta = _client.fetch(PRODUCT, VERSION, limit=PAGE_SIZE, offset=len(rows))
            if total is not None and meta.get("total_count") != total:
                log.warning("Row count changed from %s to %s while loading -- starting over.",
                            total, meta.get("total_count"))
                break
            total = meta.get("total_count")
            rows.extend(page)

            if len(page) < PAGE_SIZE or total is None or len(rows) >= total:
                return rows, meta

    raise DataProductError("The data set kept changing while loading -- gave up after two tries.")


def load_materials() -> pl.DataFrame:
    """Fetches the data product from the API layer and shapes it for the table."""
    rows, meta = _all_pages()
    total = meta.get("total_count") or len(rows)

    log.info("Snapshot %s | source %s | %s of %s rows | cache %s",
             meta.get("generated_at"), meta.get("source"),
             len(rows), total, meta.get("cache"))

    if len(rows) < total:
        # The API announced more rows than it handed out. The table then looks
        # complete but is not, and the KPI tiles count too little.
        log.warning("Data product incomplete: %s of %s rows loaded.", len(rows), total)
    if total > EXPECTED_MAX_ROWS:
        log.warning("Data product has %s rows -- more than the %s this dashboard is sized for.",
                    total, EXPECTED_MAX_ROWS)
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
    fresh_until = float(_CACHE.get("expires_at", 0))  # type: ignore[arg-type]
    if not force_reload and _CACHE.get("frame") is not None and time.monotonic() < fresh_until:
        return _CACHE["frame"]  # type: ignore[return-value]

    try:
        frame = load_materials()
    except DataProductError as exc:
        if _CACHE.get("frame") is not None:
            log.warning("API unreachable (%s) -- serving the last snapshot.", exc)
            return _CACHE["frame"]  # type: ignore[return-value]
        log.error("API unreachable and no snapshot in the cache: %s", exc)
        raise

    _CACHE["frame"] = frame
    _CACHE["expires_at"] = time.monotonic() + CACHE_TTL_SECONDS
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

    Limitation: if Dash runs with several worker processes, this only clears
    the cache of the process that handled the click. The others show the old
    snapshot until the TTL expires.
    """
    _CACHE.clear()


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
