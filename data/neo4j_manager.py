"""
Verwalteter Neo4j-Treiber.

Ein Neo4jManager kapselt den Lebenszyklus des Treibers: einmal öffnen,
garantiert schließen. Verwendung wie im Team üblich, in app.py:

    with Neo4jManager(uri=..., auth=..., db_name="neo4j") as db:
        app.run()

Erreichbarkeit über Modulgrenzen
--------------------------------
Dash läuft in EINEM Prozess; Callbacks und der `with`-Block teilen sich den
Speicher. Damit die Datenzugriffsschicht (data/repository.py) an GENAU die
Instanz kommt, die app.py geöffnet hat, veröffentlicht sich der Manager beim
Betreten selbst in `_ACTIVE`. Das Repository holt sie über `get_manager()`.

Warum nicht einfach in app.py auf `db` zugreifen? app.py importiert bereits
alle Callbacks -- der umgekehrte Import (Repository -> app) wäre ein Zirkel.
Der Manager kennt niemanden und ist deshalb der saubere Ankerpunkt.
"""
from __future__ import annotations

import logging

from neo4j import GraphDatabase

log = logging.getLogger(__name__)

# Die eine, aktuell aktive Instanz (pro Prozess). Wird von __enter__ gesetzt
# und von close()/__exit__ wieder geleert.
_ACTIVE: "Neo4jManager | None" = None


def get_manager() -> "Neo4jManager":
    """Liefert den aktiven Neo4jManager oder wirft, wenn keiner läuft."""
    if _ACTIVE is None:
        raise RuntimeError(
            "Kein aktiver Neo4jManager. app.py muss die App innerhalb von "
            "'with Neo4jManager(...)' starten (und NEO4J_URI muss gesetzt sein)."
        )
    return _ACTIVE


def _coerce_auth(auth):
    """Macht aus 'user/passwort' bzw. 'user:passwort' ein (user, pw)-Tupel.

    Ein Tupel/None wird unverändert durchgereicht. Passt euer Team eine andere
    Konvention für NEO4J_AUTH an, ist DAS hier die eine Stelle dafür.
    """
    if isinstance(auth, str) and ("/" in auth or ":" in auth):
        sep = "/" if "/" in auth else ":"
        user, _, pw = auth.partition(sep)
        return (user, pw)
    return auth


class Neo4jManager:
    def __init__(self, uri, auth, db_name: str = "neo4j"):
        self.uri = uri
        self.auth = auth
        self.db_name = db_name
        self.driver = None

    def __enter__(self) -> "Neo4jManager":
        global _ACTIVE
        # Ohne URI kein Treiber: Dev läuft dann über die Mock-Daten weiter
        # (data/repository.py fällt zurück, wenn kein Manager aktiv ist).
        if not self.uri:
            log.warning(
                "NEO4J_URI nicht gesetzt -- Neo4jManager im Leerlauf, "
                "die App nutzt Mock-Daten."
            )
            return self

        self.driver = GraphDatabase.driver(self.uri, auth=_coerce_auth(self.auth))
        self.driver.verify_connectivity()
        _ACTIVE = self
        log.info("Neo4j verbunden: %s (db=%s)", self.uri, self.db_name)
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        global _ACTIVE
        if self.driver is not None:
            self.driver.close()
            self.driver = None
        if _ACTIVE is self:
            _ACTIVE = None

    # ----------------------------------------------------------------------
    def fetch_dataframe(self, cypher: str, params: dict | None = None):
        """Führt eine Cypher-Abfrage aus und gibt das Ergebnis als Polars-DF.

        Bewusst generisch (Cypher rein, DataFrame raus) -- die konkreten
        Abfragen stehen bei den Aufrufern (z. B. data/repository.py).
        """
        import polars as pl

        records, _summary, _keys = self.driver.execute_query(
            cypher, params or {}, database_=self.db_name
        )
        return pl.DataFrame([r.data() for r in records])
