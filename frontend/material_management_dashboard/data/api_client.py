"""
HTTP-Client fuer den API-Layer.

WAS IST DAS?
============
Das Dashboard spricht nicht mehr selbst mit Neo4j, sondern fragt den API-Layer
nach fertigen "Datenprodukten". Dieses Modul ist die eine Stelle, die HTTP
kennt -- alles andere im Dashboard sieht weiterhin nur einen DataFrame.

    Frueher:  Dashboard --Bolt/Cypher--> Neo4j
    Jetzt:    Dashboard --HTTP/JSON----> API-Layer --> Neo4j / Postgres / ...

HERKUNFT
========
Kopie von `api/src/data_api/clients/dash_client.py`. Diese Datei ist die
Vorlage; aendert sie sich dort, wird sie hier nachgezogen.

Warum kopiert und nicht importiert? Weil `api/` ein eigenes Projekt mit eigener
virtueller Umgebung ist -- es haengt an FastAPI, dem Neo4j-Treiber und
SQLAlchemy. Nichts davon soll ins Dashboard, das nur `httpx` braucht. Sobald das
dritte Dashboard diesen Client benutzt, lohnt sich ein kleines gemeinsames
Paket; bei zweien ist Kopieren billiger als die Paketverwaltung.

WARUM SYNCHRON?
===============
Dash-Callbacks sind normale, synchrone Funktionen. Ein `asyncio.run()` darin
waere ein Fehler mit Ansage. Der API-Server ist intern async -- das ist seine
Sache und fuer den Client unsichtbar.
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
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`transport` dient nur Tests: mit `httpx.MockTransport` laesst sich der
        Client ohne laufenden Server pruefen (siehe tests/test_repository.py)."""
        self._base_url = (base_url or os.getenv("DATA_API_URL", "http://localhost:8000")).rstrip("/")
        headers = {"Accept": "application/json"}
        if api_key or os.getenv("DATA_API_KEY"):
            headers["X-API-Key"] = api_key or os.environ["DATA_API_KEY"]
        # EIN Client pro Dashboard-Prozess: er haelt den Connection-Pool offen.
        # Ein `httpx.get(...)` pro Callback baut jedes Mal neu auf.
        self._client = httpx.Client(base_url=self._base_url, headers=headers,
                                    timeout=timeout, transport=transport)
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


