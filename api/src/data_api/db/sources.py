"""
Der Zugang zu den Datenquellen.

Ein Datenprodukt bekommt genau ein Objekt herein -- `Sources` -- und stellt
damit seine Abfragen:

    rows = await sources.neo4j(CYPHER)
    rows = await sources.postgres(SQL, seit=params.seit)

Mehr gibt es nicht zu wissen. `Sources` kuemmert sich um drei Dinge, die man
sonst in jedem Datenprodukt neu richtig machen muesste:

  1. Die Verbindung wird erst geoeffnet, wenn sie gebraucht wird. Ein Produkt,
     das nur den Graphen abfragt, oeffnet keine Postgres-Verbindung.
  2. Zwei Abfragen im selben Request teilen sich eine Verbindung.
  3. Alles wird am Ende zuverlaessig geschlossen -- auch wenn die Abfrage
     einen Fehler wirft. Darum steht in keinem Datenprodukt je `session.close()`.

Ein `Sources`-Objekt lebt genau einen HTTP-Request lang (siehe api/deps.py).
"""
from __future__ import annotations

import base64
import logging
from contextlib import AsyncExitStack
from typing import Any

from neo4j import AsyncDriver
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from neo4j.graph import Entity, Path
from neo4j.spatial import Point
from neo4j.time import Date, DateTime, Duration, Time
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from data_api.core.config import Settings
from data_api.core.errors import ConfigurationError, UpstreamUnavailableError
from data_api.db.sql import SessionMaker

log = logging.getLogger(__name__)

# Eine Ergebniszeile ist ein einfaches dict: Spaltenname -> Wert.
Row = dict[str, Any]


def _als_python_wert(wert: Any) -> Any:
    """Uebersetzt Neo4j-eigene Typen in solche, die Pydantic und JSON kennen.

    Der Treiber liefert fuer manche Property-Typen eigene Klassen zurueck. Ohne
    diese Umwandlung passiert eines von zwei Dingen -- beide unangenehm:

        neo4j.time.Date      -> Pydantic lehnt ab (auch fuer ein date-Feld!):
                                "Input should be a valid date"
        neo4j.time.Duration  -> erbt von tuple und landet als nacktes [3,2,0,0]
        neo4j.spatial.Point  -> erbt von tuple und landet als nacktes [1.0,2.0]
        neo4j.graph.Path     -> geht als Treiberobjekt durch; FastAPI faellt auf
                                vars() zurueck und liefert private Attributnamen

    Der zweite Fall ist der gefaehrlichere: kein Fehler, aber die Bedeutung ist
    weg. Deshalb wird hier alles explizit uebersetzt.

    Rekursiv, weil `collect()` und Map-Projektionen verschachtelte Listen und
    dicts liefern.
    """
    # Reihenfolge beachten: Duration und Point erben von tuple, muessen also
    # VOR der allgemeinen Listenbehandlung geprueft werden.
    if isinstance(wert, (Date, DateTime, Time)):
        return wert.to_native()             # -> datetime.date / .datetime / .time
    if isinstance(wert, Duration):
        return wert.iso_format()            # -> "P3M2DT1M30S"
    if isinstance(wert, Point):
        # `z` wird IMMER gesetzt (bei 2D auf None). Sonst haette ein 2D-Punkt
        # die Form {"srid","x","y"} und ein 3D-Punkt {"srid","x","y","z"} --
        # eine Antwort mit gemischten Zeilen haette dann uneinheitliche Objekte,
        # und ein Zeilenmodell mit `z: float | None` waere nicht erfuellbar.
        koordinaten = dict(zip(("x", "y", "z"), tuple(wert)))
        return {"srid": wert.srid, "x": koordinaten.get("x"),
                "y": koordinaten.get("y"), "z": koordinaten.get("z")}
    if isinstance(wert, Path):
        # Ein Pfad ist KEIN Entity und faellt sonst unuebersetzt durch --
        # bei Stuecklisten und Lieferketten der naheliegende Rueckgabewert.
        return {
            "nodes": [_als_python_wert(k) for k in wert.nodes],
            "relationships": [_als_python_wert(b) for b in wert.relationships],
        }
    if isinstance(wert, Entity):
        # Node oder Relationship. Nur die Properties -- Labels, Typ und
        # Element-ID gehen dabei verloren. Deshalb im Cypher besser die
        # gewuenschten Felder einzeln zurueckgeben (RETURN m.nr AS nr) statt
        # den ganzen Knoten (RETURN m).
        return {name: _als_python_wert(v) for name, v in dict(wert).items()}
    if type(wert).__module__.split(".")[0] == "neo4j":
        # Auffangnetz: MUSS vor der Container-Behandlung stehen --
        # mehrere Treibertypen erben von tuple und wuerden sonst still zur
        # Liste (genau der Duration/Point-Fehler eine Ebene hoeher).
        # ein Treibertyp, den diese Funktion nicht kennt, wuerde
        # sonst still durchgereicht -- genau der Fehler, den sie verhindern
        # soll. Lieber laut hier als spaeter unverstaendlich in der Antwort.
        raise TypeError(
            f"Nicht uebersetzbarer Neo4j-Typ: {type(wert).__module__}."
            f"{type(wert).__name__}. Bitte in db/sources.py::_als_python_wert "
            f"ergaenzen oder im Cypher in einen einfachen Wert umwandeln."
        )
    if isinstance(wert, dict):
        return {name: _als_python_wert(v) for name, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_als_python_wert(v) for v in wert]
    if isinstance(wert, (bytes, bytearray)):
        return base64.b64encode(wert).decode("ascii")
    return wert


class Sources:
    def __init__(
        self,
        stack: AsyncExitStack,
        settings: Settings,
        neo4j_driver: AsyncDriver | None,
        sql_sessionmaker: SessionMaker | None,
    ) -> None:
        self._stack = stack
        self._settings = settings
        self._driver = neo4j_driver
        self._sessionmaker = sql_sessionmaker
        self._sessions: dict[str, Any] = {}
        # Welche Quellen dieser Request benutzt hat -> landet in meta.source.
        self.used: set[str] = set()

    async def neo4j(self, cypher: str, **parameter: Any) -> list[Row]:
        """Fuehrt eine Cypher-Abfrage aus und gibt die Zeilen zurueck.

            rows = await sources.neo4j("MATCH (m:Material) RETURN m.nr AS nr")

        Parameter werden als benannte Werte uebergeben, NICHT in den Text
        eingesetzt -- `$seit` im Cypher, `seit=...` hier. Das ist schneller
        (Neo4j kann den Abfrageplan wiederverwenden) und sicher.

        Neo4j-eigene Typen (Date, Duration, Point ...) werden dabei in normale
        Python-Werte uebersetzt -- siehe `_als_python_wert`.
        """
        if self._driver is None:
            raise ConfigurationError(
                "Neo4j ist nicht konfiguriert (NEO4J_URI fehlt), wird aber gebraucht."
            )
        if "neo4j" not in self._sessions:
            self._sessions["neo4j"] = await self._stack.enter_async_context(
                self._driver.session(database=self._settings.neo4j_db)
            )
        self.used.add("neo4j")
        # `parameters=` explizit statt `**parameter`: die Treibersignatur ist
        # `run(query, parameters=None, **kwargs)`. Ein Cypher-Parameter, der
        # zufaellig `query` oder `parameters` heisst, landete sonst im Slot des
        # Treibers statt in der Abfrage -- einmal als TypeError, einmal als
        # stiller Fehlgriff.
        try:
            result = await self._sessions["neo4j"].run(cypher, parameters=parameter)
            # Bewusst nicht `result.data()`: das wuerde Knoten still zu
            # Properties verflachen, ohne dass wir die Werte darin uebersetzen
            # koennen.
            return [
                {name: _als_python_wert(wert) for name, wert in record.items()}
                async for record in result
            ]
        except (ServiceUnavailable, SessionExpired, OSError) as fehler:
            # 503 statt 500: fuer das Dashboard ist das der Unterschied zwischen
            # "spaeter nochmal versuchen" und "bitte als Bug melden".
            raise UpstreamUnavailableError(f"Neo4j nicht erreichbar: {fehler}") from fehler

    async def postgres(self, sql: str, **parameter: Any) -> list[Row]:
        """Fuehrt eine SQL-Abfrage aus und gibt die Zeilen zurueck.

            rows = await sources.postgres("SELECT * FROM x WHERE d >= :seit", seit=...)

        Wie oben: `:name` im SQL, `name=...` hier. Werte niemals in den Text
        einsetzen -- das waere eine SQL-Injection-Luecke.
        """
        if self._sessionmaker is None:
            raise ConfigurationError(
                "Postgres ist nicht konfiguriert (POSTGRES_DSN fehlt), wird aber gebraucht."
            )
        if "sql" not in self._sessions:
            self._sessions["sql"] = await self._stack.enter_async_context(
                self._sessionmaker()
            )
        self.used.add("postgres")
        try:
            result = await self._sessions["sql"].execute(text(sql), parameter)
            return [dict(row) for row in result.mappings()]
        except (OperationalError, DBAPIError, OSError) as fehler:
            raise UpstreamUnavailableError(f"Postgres nicht erreichbar: {fehler}") from fehler

    async def commit(self) -> None:
        """Schliesst die SQL-Transaktion ab. Wird vom Request-Scope aufgerufen.

        Ohne diesen Aufruf rollt SQLAlchemy beim Schliessen der Session zurueck.
        Fuer die reine Leseseite ist das folgenlos -- aber wer dem TODO in
        api/v1/mappings.py folgt und ein INSERT ergaenzt, bekaeme sonst einen
        Endpunkt, der 201 antwortet, den Cache invalidiert, Erfolg loggt und
        nichts schreibt. Wieder still, wieder plausibel aussehend.

        Wird NUR auf dem Erfolgspfad aufgerufen (siehe api/deps.py): bei einer
        Ausnahme bleibt es beim Rollback durch den AsyncExitStack.
        """
        sitzung = self._sessions.get("sql")
        if sitzung is not None:
            await sitzung.commit()

    @property
    def label(self) -> str:
        """Fuer meta.source in der Antwort, z. B. 'neo4j+postgres'."""
        return "+".join(sorted(self.used)) or "none"
