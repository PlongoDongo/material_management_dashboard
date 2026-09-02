"""
Datenzugriffsschicht des Dashboards.

Diese Datei ist die EINZIGE Stelle, die weiss, woher die Daten kommen. Der Rest
des Dashboards ruft `get_materials()` auf und bekommt einen Polars-DataFrame --
genau wie vorher. Deshalb war der Umstieg von Neo4j auf den API-Layer eine
Aenderung an dieser einen Datei (plus dem Wegfall von `data/neo4j.py`).

    frueher:  get_materials() -> Cypher gegen Neo4j -> DataFrame
    jetzt:    get_materials() -> HTTP an den API-Layer -> DataFrame

Was das Dashboard dadurch NICHT mehr braucht:
  * den neo4j-Treiber und dessen Zugangsdaten
  * Kenntnis des Graphmodells (Cypher)
  * eine eigene Vorstellung davon, was "Bestandswert" bedeutet

ZWEI NAMENSRAEUME, EINE GRENZE
==============================
Der API-Vertrag und die Spaltennamen der Tabelle sind NICHT dasselbe. Die API
liefert `werk_id` und `werk_name`; das Dashboard hat historisch eine Spalte
`werk`. Statt jetzt Filter, Callbacks, Sidebar und Tests umzubenennen, wird an
dieser Grenze uebersetzt -- siehe `_API_TO_UI`.

Das ist Absicht und kein Notbehelf: Die API gehoert einem anderen Team und darf
ihre Felder umbenennen, ohne dass das Dashboard bricht. Was sich dann aendert,
ist eine Zeile hier. Wichtig ist nur, dass die Uebersetzung SICHTBAR an einer
Stelle steht und nicht ueber den Code verstreut ist.
"""
from __future__ import annotations

import logging
import os
import time

import polars as pl

from data.api_client import DataProductClient, DataProductError
from data.schema import COLUMN_LABELS, COLUMNS  # noqa: F401  (Re-Export)

log = logging.getLogger(__name__)

# Welches Datenprodukt in welcher Version. Bewusst fest verdrahtet und nicht
# "latest": ein Versionswechsel soll im Git-Diff auftauchen und getestet werden,
# nicht still passieren, weil die API ein neues Major ausgerollt hat.
PRODUCT = "material-overview"
VERSION = "v2"

# Obergrenze der Abfrage. Zugleich das Maximum von ProductParams.limit --
# mehr geht serverseitig nicht. Wird sie erreicht, MUSS das auffallen (siehe
# load_materials): eine vollstaendig aussehende Tabelle mit unvollstaendigen
# Daten ist schlimmer als eine Fehlermeldung, und die KPI-Kacheln zaehlen die
# fehlenden Zeilen ebenfalls nicht mit.
MAX_ZEILEN = 50_000

# Wie lange ein einmal geholter Datenstand im Dashboard-Prozess gilt.
# Der Server cached zusaetzlich (cache_ttl des Datenprodukts); dieser Cache hier
# spart den HTTP-Roundtrip bei jedem Callback.
CACHE_TTL_SECONDS = int(os.getenv("DATA_CACHE_TTL", "60"))

# API-Feld -> Dashboard-Spalte. Nur was hier steht, landet in der Tabelle.
# Felder der API, die das Dashboard nicht braucht (werk_id, preis), fehlen
# bewusst -- neue Felder der API brechen das Dashboard dadurch nie.
_API_TO_UI: dict[str, str] = {
    "material_nr": "material_nr",
    "bezeichnung": "bezeichnung",
    "warengruppe": "warengruppe",
    "werk_name": "werk",          # <- die einzige echte Umbenennung
    "status": "status",
    "bestand": "bestand",
    "bestandswert": "bestandswert",
    "geaendert": "geaendert",
}

# EIN Client pro Prozess. Er haelt den Connection-Pool offen; ein
# `httpx.get(...)` pro Callback wuerde jedes Mal neu verbinden.
_client = DataProductClient()

# Cache-Container statt eines nackten Modul-Globals, damit die Funktion unten
# den Namen nicht per `global` neu binden muss.
_CACHE: dict[str, object] = {}


def _rows_to_frame(rows: list[dict]) -> pl.DataFrame:
    """API-Zeilen -> DataFrame mit den Dashboard-Spalten.

    Rein und ohne HTTP: bekommt Zeilen herein, gibt einen DataFrame zurueck.
    Dadurch ohne laufende API testbar (siehe tests/test_repository.py).
    """
    if not rows:
        # Leerer, aber SCHEMA-KORREKTER Frame. Ohne Schema wuerde die Tabelle
        # beim ersten leeren Ergebnis mit "column not found" abstuerzen.
        return pl.DataFrame(
            schema={c: (pl.Int64 if c in ("bestand",) else
                        pl.Float64 if c == "bestandswert" else pl.Utf8)
                    for c in COLUMNS}
        )

    frame = pl.DataFrame(rows)
    # Nur bekannte Felder uebernehmen und auf die UI-Namen umbenennen.
    vorhanden = {api: ui for api, ui in _API_TO_UI.items() if api in frame.columns}
    frame = frame.select(list(vorhanden)).rename(vorhanden)

    # Fehlende Spalten ergaenzen: liefert die API ein Feld (noch) nicht, soll
    # die Tabelle trotzdem rendern statt zu fliegen.
    for column in COLUMNS:
        if column not in frame.columns:
            log.warning("Datenprodukt %s/%s liefert kein Feld fuer Spalte '%s'.",
                        PRODUCT, VERSION, column)
            frame = frame.with_columns(pl.lit(None).alias(column))

    return frame.select(COLUMNS).with_columns(
        pl.col("bestand").cast(pl.Int64, strict=False)
    )


def load_materials() -> pl.DataFrame:
    """Holt das Datenprodukt beim API-Layer und formt es fuer die Tabelle."""
    rows, meta = _client.fetch(PRODUCT, VERSION, limit=MAX_ZEILEN)
    log.info("Datenstand %s | Quelle %s | %s Zeilen | Cache %s",
             meta.get("generated_at"), meta.get("source"),
             meta.get("total_count"), meta.get("cache"))

    gesamt = meta.get("total_count") or len(rows)
    if gesamt > len(rows):
        # Nicht nur loggen: geloggte Fehler sieht im Betrieb niemand, und die
        # Zahl im Management-Meeting waere dann falsch. Die UI zeigt den
        # Hinweis neben dem Zeilenzaehler (siehe tabs/data_overview.py).
        log.error("Datenprodukt gekuerzt: %s von %s Zeilen geladen (limit=%s). "
                  "KPI-Kacheln und Zaehler sind unvollstaendig.",
                  len(rows), gesamt, MAX_ZEILEN)
        _CACHE["gekuerzt"] = (len(rows), gesamt)
    else:
        _CACHE.pop("gekuerzt", None)

    if meta.get("deprecated"):
        log.warning("Datenprodukt %s/%s ist abgekuendigt (Sunset %s) -- bitte migrieren.",
                    PRODUCT, VERSION, meta.get("sunset"))
    return _rows_to_frame(rows)


def get_materials(*, force_reload: bool = False) -> pl.DataFrame:
    """Gecachter Zugriff. Das ist die Funktion, die der Rest des Dashboards nutzt.

    Verhalten bei API-Ausfall: Gibt es noch einen (abgelaufenen) Stand im Cache,
    wird dieser weiter ausgeliefert und eine Warnung geloggt -- ein Dashboard,
    das kurz veraltete Zahlen zeigt, ist besser als eines, das leer ist. Gibt es
    keinen, wird der Fehler durchgereicht, statt stillschweigend eine leere
    Tabelle zu zeigen.
    """
    frisch_bis = float(_CACHE.get("expires_at", 0))  # type: ignore[arg-type]
    if not force_reload and _CACHE.get("frame") is not None and time.monotonic() < frisch_bis:
        return _CACHE["frame"]  # type: ignore[return-value]

    try:
        frame = load_materials()
    except DataProductError as exc:
        if _CACHE.get("frame") is not None:
            log.warning("API nicht erreichbar (%s) -- liefere den letzten Stand weiter.", exc)
            return _CACHE["frame"]  # type: ignore[return-value]
        log.error("API nicht erreichbar und kein Stand im Cache: %s", exc)
        raise

    _CACHE["frame"] = frame
    _CACHE["expires_at"] = time.monotonic() + CACHE_TTL_SECONDS
    return frame


def kuerzung() -> tuple[int, int] | None:
    """(geladen, gesamt), falls der letzte Abruf gekuerzt wurde -- sonst None.

    Die UI liest das, um den Zeilenzaehler zu kennzeichnen. Eine Tabelle, die
    vollstaendig aussieht und es nicht ist, ist die teuerste Fehlerklasse.
    """
    return _CACHE.get("gekuerzt")  # type: ignore[return-value]


def distinct_values(column: str) -> list[str]:
    """Eindeutige, sortierte Werte einer Spalte -- fuer die Filter-Dropdowns."""
    values = (
        get_materials()
        .select(pl.col(column))
        .drop_nulls()
        .to_series()
        .to_list()
    )
    return sorted({v for v in values if v not in (None, "")})
