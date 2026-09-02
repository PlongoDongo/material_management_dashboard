"""
HTTP-Client fuer den API-Layer.

Das Dashboard spricht nicht mehr selbst mit Neo4j, sondern fragt den API-Layer
nach fertigen "Datenprodukten". Dieses Modul ist die eine Stelle, die HTTP
kennt -- alles andere im Dashboard sieht weiterhin nur einen DataFrame.

    frueher:  Dashboard --Bolt/Cypher--> Neo4j
    jetzt:    Dashboard --HTTP/JSON----> API-Layer --> Neo4j / Postgres

Benutzung (siehe data/repository.py):

    client = DataProductClient()                       # einmal pro Prozess
    rows, meta = client.fetch("material-overview", "v2", limit=50_000)

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

# Eine Zeile ist ein dict, die Metadaten sind ein dict. Diese beiden Namen
# machen die Signaturen unten lesbar.
Row = dict[str, Any]
Meta = dict[str, Any]


class DataProductError(RuntimeError):
    """Die API war nicht erreichbar oder hat einen Fehler gemeldet."""


class DataProductClient:
    """Haelt EINE HTTP-Verbindung offen und holt damit Datenprodukte."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`transport` dient nur Tests (httpx.MockTransport) -- sonst leer lassen."""
        url = base_url or os.getenv("DATA_API_URL", "http://localhost:8000")
        schluessel = api_key or os.getenv("DATA_API_KEY")

        headers = {"Accept": "application/json"}
        if schluessel:
            headers["X-API-Key"] = schluessel

        # EIN Client pro Prozess: er haelt den Verbindungspool offen. Ein
        # `httpx.get(...)` pro Callback wuerde jedes Mal neu verbinden --
        # derselbe Gedanke wie beim Datenbanktreiber im Server.
        self._client = httpx.Client(
            base_url=url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def fetch(self, product: str, version: str, **filter: Any) -> tuple[list[Row], Meta]:
        """Holt ein Datenprodukt. Gibt (Zeilen, Metadaten) zurueck.

            rows, meta = client.fetch("material-overview", "v2", status=["Gesperrt"])

        Listen werden zu wiederholten Query-Parametern
        (?status=Aktiv&status=Gesperrt) -- genau das erwartet die API.
        Leere Werte werden weggelassen, damit `status=None` nicht als Filter zaehlt.
        """
        pfad = f"/api/v1/data-products/{product}/{version}"
        parameter = {name: wert for name, wert in filter.items() if wert not in (None, [], "")}

        try:
            antwort = self._client.get(pfad, params=parameter)
        except httpx.HTTPError as fehler:
            raise DataProductError(f"API nicht erreichbar: {fehler}") from fehler

        if antwort.status_code >= 400:
            raise DataProductError(f"{product}/{version}: {_fehlertext(antwort)}")

        inhalt = antwort.json()
        meta = inhalt["meta"]
        if meta.get("deprecated"):
            log.warning("Datenprodukt %s/%s ist abgekuendigt (Sunset: %s) -- bitte migrieren.",
                        product, version, meta.get("sunset"))
        return inhalt["data"], meta

    def catalog(self) -> list[Row]:
        """Welche Datenprodukte gibt es? Nuetzlich zum Nachschauen."""
        antwort = self._client.get("/api/v1/catalog")
        if antwort.status_code >= 400:
            raise DataProductError(_fehlertext(antwort))
        return antwort.json()

    def close(self) -> None:
        self._client.close()


def _fehlertext(antwort: httpx.Response) -> str:
    """Macht aus einer Fehlerantwort eine lesbare Meldung.

    Die API antwortet im Problem-Details-Format ({"title": ..., "detail": ...}).
    Falls doch etwas anderes kommt (z. B. ein Proxy dazwischen), wird der
    Rohtext gekuerzt.
    """
    try:
        inhalt = antwort.json()
        return f"{antwort.status_code} {inhalt.get('title')}: {inhalt.get('detail')}"
    except Exception:                                  # noqa: BLE001
        return f"{antwort.status_code} {antwort.text[:200]}"


