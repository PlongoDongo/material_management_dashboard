"""
HTTP client for the API layer.

The dashboard no longer talks to Neo4j itself; it asks the API layer for
finished "data products". This module is the only place that knows about HTTP --
everything else in the dashboard still just sees a DataFrame.

    before:  dashboard --Bolt/Cypher--> Neo4j
    now:     dashboard --HTTP/JSON----> API layer --> Neo4j / Postgres

Usage (see data/repository.py):

    client = DataProductClient()                       # once per process
    rows, meta = client.fetch("material-overview", "v3", limit=50_000)

ORIGIN
======
A copy of `api/src/data_api/clients/dash_client.py`. That file is the template;
when it changes, this one is updated to match. `tests/test_repository.py`
compares the two, so the copy cannot drift silently.

Why copied and not imported? Because `api/` is a separate project with its own
virtual environment -- it depends on FastAPI, the Neo4j driver and SQLAlchemy.
None of that belongs in the dashboard, which only needs `httpx`. Once a third
dashboard uses this client, a small shared package becomes worthwhile; with two,
copying is cheaper than the packaging.

WHY SYNCHRONOUS?
================
Dash callbacks are ordinary, synchronous functions. An `asyncio.run()` inside
one would be a mistake waiting to happen. The API server works asynchronously
internally -- that is its business and invisible here.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

# A row is a dict and the metadata is a dict. These two names keep the
# signatures below readable.
Row = dict[str, Any]
Meta = dict[str, Any]


class DataProductError(RuntimeError):
    """The API was unreachable or reported an error."""


class DataProductClient:
    """Keeps ONE HTTP connection open and fetches data products through it."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`transport` is for tests only (httpx.MockTransport) -- leave it unset."""
        url = base_url or os.getenv("DATA_API_URL", "http://localhost:8000")
        key = api_key or os.getenv("DATA_API_KEY")

        headers = {"Accept": "application/json"}
        if key:
            headers["X-API-Key"] = key

        # One client per process: it keeps the connection pool open. An
        # `httpx.get(...)` per callback would reconnect every time -- the same
        # reasoning as for the database driver in the server.
        self._client = httpx.Client(
            base_url=url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def fetch(self, product: str, version: str, **filters: Any) -> tuple[list[Row], Meta]:
        """Fetches a data product. Returns (rows, metadata).

            rows, meta = client.fetch("material-overview", "v3", status=["Gesperrt"])

        Lists become repeated query parameters
        (?status=Aktiv&status=Gesperrt) -- exactly what the API expects.
        Empty values are dropped so that `status=None` does not count as a filter.
        """
        path = f"/api/v1/data-products/{product}/{version}"
        parameters = {name: value for name, value in filters.items() if value not in (None, [], "")}

        try:
            response = self._client.get(path, params=parameters)
        except httpx.HTTPError as error:
            raise DataProductError(f"API unreachable: {error}") from error

        if response.status_code >= 400:
            raise DataProductError(f"{product}/{version}: {_error_text(response)}")

        body = response.json()
        meta = body["meta"]
        if meta.get("deprecated"):
            log.warning("Data product %s/%s is deprecated (sunset: %s) -- please migrate.",
                        product, version, meta.get("sunset"))
        return body["data"], meta

    def catalog(self) -> list[Row]:
        """Which data products exist? Useful for looking things up."""
        response = self._client.get("/api/v1/catalog")
        if response.status_code >= 400:
            raise DataProductError(_error_text(response))
        return response.json()

    def close(self) -> None:
        self._client.close()


def _error_text(response: httpx.Response) -> str:
    """Turns an error response into a readable message.

    The API answers in Problem Details format ({"title": ..., "detail": ...}).
    If something else arrives (a proxy in between, say), the raw text is
    truncated instead.
    """
    try:
        body = response.json()
        return f"{response.status_code} {body.get('title')}: {body.get('detail')}"
    except Exception:                                  # noqa: BLE001
        return f"{response.status_code} {response.text[:200]}"


