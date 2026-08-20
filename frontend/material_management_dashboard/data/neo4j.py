"""
Neo4j-Treiber: einmal erzeugen, per Session nutzen.

Der neo4j-Treiber IST bereits „das eine Objekt, aus dem man Sessions holt":
thread-safe, hält den Connection-Pool, gedacht als einmal erzeugtes, geteiltes
Objekt. Darum keine eigene Singleton-Verwaltung mehr -- app.py legt den Treiber
an Flasks Standardstelle ab (`server.extensions["neo4j_driver"]`), die
Datenschicht holt ihn über `flask.current_app` (kein Zirkelimport).

Session-Lebensdauer: Der TREIBER ist langlebig (ein Objekt pro Prozess), eine
SESSION ist kurzlebig und nicht thread-safe -> pro Arbeitseinheit eine, via
`with driver.session() as session`.
"""
from __future__ import annotations

import atexit
import logging

from neo4j import Driver, GraphDatabase

log = logging.getLogger(__name__)

# Zugangsdaten, wie sie aus der Umgebung kommen können: fertiges Tupel,
# "user/passwort"- bzw. "user:passwort"-String oder nichts (Mock-Modus).
Auth = tuple[str, str] | str | None


def _coerce_auth(auth: Auth) -> tuple[str, str] | str | None:
    """Macht aus 'user/passwort' bzw. 'user:passwort' ein (user, pw)-Tupel.

    Tupel/None werden unverändert durchgereicht. Andere NEO4J_AUTH-Konvention?
    Dann ist DAS hier die eine Stelle dafür.
    """
    if isinstance(auth, str) and ("/" in auth or ":" in auth):
        sep = "/" if "/" in auth else ":"
        user, _, pw = auth.partition(sep)
        return (user, pw)
    return auth


def make_driver(uri: str | None, auth: Auth) -> Driver | None:
    """Erzeugt den (einen) Treiber. Ohne URI: None -> die App läuft mit Mock-Daten.

    Der Treiber wird beim Import angelegt (nicht in einem `with`-Block), damit er
    auch unter gunicorn (`app:server`) existiert. `atexit` schließt ihn beim
    Prozessende sauber.
    """
    if not uri:
        log.warning("NEO4J_URI nicht gesetzt -- Neo4j inaktiv, die App nutzt Mock-Daten.")
        return None

    driver = GraphDatabase.driver(uri, auth=_coerce_auth(auth))
    driver.verify_connectivity()
    atexit.register(driver.close)
    log.info("Neo4j verbunden: %s", uri)
    return driver
