"""
Client for the Dash apps -- a template to copy.

This file is copied into each dashboard (e.g. as `data/api_client.py`) rather
than imported: `api/` depends on FastAPI, the Neo4j driver and SQLAlchemy --
none of which belong in a dashboard that only needs `httpx`.

Usage:

    client = DataProductClient()                       # once per process
    rows, meta = client.fetch("material-overview", "v3", limit=50_000)

`rows` is a list of dicts, `meta` is the response metadata (version, timestamp,
source, row count).

Why synchronous and not async? Dash callbacks are ordinary, synchronous
functions. An `asyncio.run()` inside one would be a mistake waiting to happen.
That the server works asynchronously internally is its own business and
invisible here.
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


# --- Worked example ---------------------------------------------------------
#
# The material management dashboard already uses this client:
#
#   frontend/material_management_dashboard/data/api_client.py   (the copy)
#   frontend/material_management_dashboard/data/repository.py   (the usage)
#   frontend/material_management_dashboard/tests/test_repository.py
#       -> shows how to test it with httpx.MockTransport, without a server
#
# In short:
#
#   _client = DataProductClient()          # once per process (keeps the pool)
#
#   def load_materials() -> pl.DataFrame:
#       rows, meta = _client.fetch("material-overview", "v3", limit=50_000)
#       return _rows_to_frame(rows)        # API fields -> table columns
#
# Possible extension: the API sends an ETag with every response. Sending
# `If-None-Match` back would yield an empty "304 Not Modified" when nothing
# changed, saving the transfer. Deliberately not built in -- it costs
# readability and only pays off with frequent polling.
