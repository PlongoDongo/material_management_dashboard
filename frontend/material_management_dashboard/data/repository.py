"""
Datenzugriffsschicht (Repository).

Aktuell: synthetische Mock-Daten als Polars DataFrame.
Später: Hier steckst du deine Neo4j-Abfrage ein -- siehe `load_materials()`.
Der Rest der App ruft NUR `get_materials()` auf und weiß nichts von der
Herkunft der Daten. Dadurch bleibt der Neo4j-Wechsel eine Ein-Datei-Änderung.
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import polars as pl
from flask import current_app, has_app_context

# Spaltenschema = EINE Wahrheit (data/schema.py). Re-Export, damit bestehende
# Importe `from data.repository import COLUMNS` gültig bleiben.
from data.schema import COLUMN_LABELS, COLUMNS  # noqa: F401

if TYPE_CHECKING:  # nur für Typannotation -- kein Import zur Laufzeit nötig
    from neo4j import Session

log = logging.getLogger(__name__)

# Cypher für die Materialdaten. Muss dieselben Spalten wie COLUMNS liefern,
# dann funktioniert der Rest der App unverändert.
_CYPHER = """
MATCH (m:Material)
OPTIONAL MATCH (m)-[:HAS_WARENGRUPPE]->(w:Warengruppe)
OPTIONAL MATCH (m)-[:LOCATED_IN]->(werk:Werk)
RETURN m.nr        AS material_nr,
       m.name      AS bezeichnung,
       w.name      AS warengruppe,
       werk.name   AS werk,
       m.status    AS status,
       m.einheit   AS einheit,
       m.bestand   AS bestand,
       m.geaendert AS geaendert
"""

# COLUMNS / COLUMN_LABELS kommen aus data/schema.py (oben importiert).

_WARENGRUPPEN = [
    "Betriebsstoffe", "Rohstoffe", "Fertigerzeugnisse", "Verpackung",
    "Ersatzteile", "Halbfabrikate", "",  # "" = ohne Klassifizierung
]
_WERKE = ["Werk Köln", "Werk Berlin", "Werk München", "Werk Hamburg"]
_STATUS = ["Aktiv", "Nicht geliefert", "Obsolet", "Gesperrt"]
_EINHEITEN = ["M", "KG", "L", "PAK", "ST"]
_BEZEICHNUNGEN = [
    "Gewindestange M10", "Sensorhalter Typ B", "Dichtungsring NBR 25",
    "Aluminiumprofil 40x40", "Steckverbinder 4-pol", "Schrumpfschlauch 6mm",
    "Edelstahlschraube M8x40", "Führungsschiene 500mm", "Kabelkanal PVC 60x40",
    "Distanzhülse 10mm", "Winkelschleifer-Scheibe", "Ölfilter Standard",
    "Antriebswelle 20mm", "Umlenkrolle 60mm", "Zahnriemen HTD-5M",
    "Filtereinsatz F7", "Kupferrohr 15mm", "Hydraulikzylinder 80mm",
]


def _make_mock_frame(n: int = 64) -> pl.DataFrame:
    """Erzeugt deterministische Mock-Daten (fester Seed -> reproduzierbar)."""
    rng = random.Random(42)
    rows = []
    for i in range(n):
        # Statusverteilung grob am Mockup orientiert
        status = rng.choices(
            _STATUS, weights=[0.55, 0.18, 0.15, 0.12], k=1
        )[0]
        # ~6 % der Materialien ohne Warengruppe
        warengruppe = rng.choices(
            _WARENGRUPPEN, weights=[0.18, 0.18, 0.18, 0.12, 0.12, 0.16, 0.06], k=1
        )[0]
        rows.append(
            {
                "material_nr": f"MAT-{100777 + i * 13}",
                "bezeichnung": rng.choice(_BEZEICHNUNGEN),
                "warengruppe": warengruppe,
                "werk": rng.choice(_WERKE),
                "status": status,
                "einheit": rng.choice(_EINHEITEN),
                "bestand": rng.randint(300, 9800),
                "geaendert": f"{rng.randint(1,28):02d}.{rng.randint(1,12):02d}.2026",
            }
        )
    return pl.DataFrame(rows, schema={c: (pl.Int64 if c == "bestand" else pl.Utf8)
                                       for c in COLUMNS})


def load_materials() -> pl.DataFrame:
    """Lädt die Materialdaten über den Neo4j-Treiber aus app.server.extensions.

    Der Treiber wird in app.py angelegt (`server.extensions["neo4j_driver"]`);
    hier holen wir ihn über `flask.current_app` -- ohne app.py zu importieren
    (kein Zirkelimport).

    Fällt auf Mock-Daten zurück, wenn kein Treiber vorhanden ist (kein
    NEO4J_URI) ODER außerhalb eines Flask-App-Kontexts (z. B. Tests/Skripte),
    damit die App auch ohne Datenbank läuft.
    """
    driver = current_app.extensions.get("neo4j_driver") if has_app_context() else None
    if driver is None:
        log.warning("Kein Neo4j-Treiber aktiv -- lade Mock-Daten.")
        return _make_mock_frame()

    db_name = current_app.config.get("NEO4J_DB", "neo4j")
    with driver.session(database=db_name) as session:
        return _materials_from_session(session)


def _materials_from_session(session: "Session") -> pl.DataFrame:
    """Reiner Kern: bekommt eine Session herein und liefert den DataFrame.

    Durch die injizierte Session ohne echten Server testbar -- Tests übergeben
    eine Fake-Session (kein Patchen von current_app/Treiber nötig).
    """
    result = session.run(_CYPHER)
    df = pl.DataFrame([record.data() for record in result])
    # bestand als Ganzzahl -- Neo4j kann je nach Property auch Float liefern.
    if "bestand" in df.columns:
        df = df.with_columns(pl.col("bestand").cast(pl.Int64, strict=False))
    return df.select(COLUMNS)


# --------------------------------------------------------------------------
# Einfaches Caching: die Daten werden einmal pro Prozess geladen.
# Für Live-Neo4j-Daten kannst du hier eine TTL/Refresh-Logik einbauen
# oder `get_materials(force_reload=True)` aufrufen.
# --------------------------------------------------------------------------
# Container statt eines nackten Modul-Namens: so wird der Cache befüllt, ohne
# dass die Funktion den Modulnamen per `global` neu binden muss.
_CACHE: dict[str, pl.DataFrame] = {}


def get_materials(*, force_reload: bool = False) -> pl.DataFrame:
    if force_reload or "materials" not in _CACHE:
        _CACHE["materials"] = load_materials()
    return _CACHE["materials"]


def distinct_values(column: str) -> list[str]:
    """Eindeutige, sortierte Werte einer Spalte -- für die Filter-Dropdowns."""
    df = get_materials()
    vals = (
        df.select(pl.col(column))
        .drop_nulls()
        .unique()
        .to_series()
        .to_list()
    )
    return sorted([v for v in vals if v not in (None, "")])
