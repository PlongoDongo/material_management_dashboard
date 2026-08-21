"""
Minimalclient fuer die Dash-Apps.

Diese Datei ist als Vorlage gedacht: sie wird in jede Dashboard-Anwendung
kopiert (oder das `api`-Paket wird als Abhaengigkeit installiert und nur dieses
Modul importiert). Sie ersetzt dort den direkten Neo4j-Zugriff.

Konkret fuer das Material-Management-Dashboard: `data/repository.py` behaelt
seine Funktion `get_materials()`, ruft darin aber diesen Client statt des
Neo4j-Treibers. Der Rest des Dashboards -- Filter, KPIs, Tabelle -- bleibt
unveraendert, weil er ohnehin nur `get_materials()` kennt.

Warum synchron (`httpx.Client`) und nicht async? Dash-Callbacks sind synchron.
Ein `asyncio.run()` im Callback waere ein Fehler mit Ansage.

Die VERSION ist hier fest verdrahtet. Das ist Absicht: sie soll bei einem
Update im Diff auftauchen, statt sich still per `latest` zu aendern.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)


class DataProductError(RuntimeError):
    """Die API war nicht erreichbar oder hat einen Fehler gemeldet."""


class DataProductClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = (base_url or os.getenv("DATA_API_URL", "http://localhost:8000")).rstrip("/")
        headers = {"Accept": "application/json"}
        if api_key or os.getenv("DATA_API_KEY"):
            headers["X-API-Key"] = api_key or os.environ["DATA_API_KEY"]
        # EIN Client pro Dashboard-Prozess: er haelt den Connection-Pool offen.
        # Ein `httpx.get(...)` pro Callback baut jedes Mal neu auf.
        self._client = httpx.Client(base_url=self._base_url, headers=headers, timeout=timeout)
        # ETag-Gedaechtnis fuer konditionales GET (spart Bandbreite beim Polling).
        self._etags: dict[str, str] = {}
        self._last: dict[str, dict[str, Any]] = {}

    def fetch(
        self, product: str, version: str, **params: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Holt ein Datenprodukt. Rueckgabe: (Zeilen, Metadaten).

        Listenparameter werden zu wiederholten Query-Parametern
        (?status=Aktiv&status=Gesperrt) -- genau das erwartet FastAPI.
        """
        path = f"/api/v1/data-products/{product}/{version}"
        query = {k: v for k, v in params.items() if v not in (None, [], "")}

        headers = {}
        if etag := self._etags.get(path):
            headers["If-None-Match"] = etag

        try:
            response = self._client.get(path, params=query, headers=headers)
        except httpx.HTTPError as exc:
            raise DataProductError(f"API nicht erreichbar: {exc}") from exc

        if response.status_code == 304:
            cached = self._last.get(path)
            if cached is not None:
                return cached["data"], {**cached["meta"], "cache": "client-304"}

        if response.status_code >= 400:
            problem = _problem_detail(response)
            raise DataProductError(f"{product}/{version}: {problem}")

        if tag := response.headers.get("ETag"):
            self._etags[path] = tag

        body = response.json()
        self._last[path] = body
        if body["meta"].get("deprecated"):
            log.warning("Datenprodukt %s/%s ist deprecated (Sunset: %s) -- bitte migrieren.",
                        product, version, body["meta"].get("sunset"))
        return body["data"], body["meta"]

    def catalog(self) -> list[dict[str, Any]]:
        response = self._client.get("/api/v1/catalog")
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()


def _problem_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        return f"{response.status_code} {body.get('title')}: {body.get('detail')}"
    except Exception:                                  # noqa: BLE001
        return f"{response.status_code} {response.text[:200]}"


# --- So sieht der Umbau im Dashboard aus ------------------------------------
#
#   # material_management_dashboard/data/repository.py
#   from data_api.clients.dash_client import DataProductClient
#   import polars as pl
#
#   _client = DataProductClient()          # einmal pro Prozess
#
#   def load_materials() -> pl.DataFrame:
#       rows, meta = _client.fetch("material-overview", "v2", limit=50_000)
#       log.info("Datenstand %s (%s)", meta["generated_at"], meta["source"])
#       return pl.DataFrame(rows)
#
# `data/neo4j.py` und der Cypher im Dashboard entfallen ersatzlos -- damit auch
# die Neo4j-Zugangsdaten in der Dashboard-Umgebung.
