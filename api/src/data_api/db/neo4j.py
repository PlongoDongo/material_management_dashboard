"""
Neo4j: Treiber-Lebenszyklus.

Die eine Regel, die man kennen muss:

    TREIBER  = langlebig, thread-safe, haelt den Connection-Pool
               -> GENAU EINER pro Prozess, erzeugt beim App-Start
    SESSION  = kurzlebig, NICHT thread-safe
               -> GENAU EINE pro Arbeitseinheit (= pro Request), danach zu

Ein haeufiger Fehler ist, pro Request einen Treiber zu bauen: das wirft den
Connection-Pool weg und macht aus jedem Request einen neuen TCP- plus
TLS-Handshake. Der andere haeufige Fehler ist eine prozessweite Session:
die ist nicht thread-safe und produziert unter Last sporadische Fehler.

Wir nutzen den ASYNCHRONEN Treiber, weil FastAPI-Endpunkte `async def` sind.
Mit dem synchronen Treiber muesste man die Endpunkte als `def` schreiben --
FastAPI schiebt die dann in einen Threadpool. Beides funktioniert, aber
mischen darf man es nicht: ein blockierender Treiberaufruf in `async def`
blockiert den kompletten Event-Loop und damit alle anderen Requests.
"""
from __future__ import annotations

import logging

from neo4j import AsyncDriver, AsyncGraphDatabase

log = logging.getLogger(__name__)

Auth = tuple[str, str] | str | None


def _coerce_auth(auth: Auth) -> tuple[str, str] | None:
    """Macht aus 'user/passwort' bzw. 'user:passwort' ein (user, pw)-Tupel.

    Gleiche Konvention wie im Dashboard (data/neo4j.py) -- dieselbe .env passt.
    """
    if isinstance(auth, str) and ("/" in auth or ":" in auth):
        sep = "/" if "/" in auth else ":"
        user, _, password = auth.partition(sep)
        return (user, password)
    if isinstance(auth, tuple):
        return auth
    return None


async def create_driver(uri: str | None, auth: Auth) -> AsyncDriver | None:
    """Erzeugt den einen Treiber. Ohne URI: None -> Datenprodukte, die Neo4j
    brauchen, melden einen Konfigurationsfehler und /readyz meldet 503.

    `verify_connectivity()` bewusst beim Start: lieber faellt der Container
    sofort um, als dass er "healthy" meldet und jeder Request 500 liefert.
    """
    if not uri:
        log.warning("NEO4J_URI nicht gesetzt -- Neo4j inaktiv.")
        return None

    driver = AsyncGraphDatabase.driver(uri, auth=_coerce_auth(auth))
    await driver.verify_connectivity()
    log.info("Neo4j verbunden: %s", uri)
    return driver


async def close_driver(driver: AsyncDriver | None) -> None:
    if driver is not None:
        await driver.close()
        log.info("Neo4j-Treiber geschlossen.")
