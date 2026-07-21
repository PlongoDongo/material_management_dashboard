"""
Datenzugriffsschicht (Repository).

Aktuell: synthetische Mock-Daten als Polars DataFrame.
Später: Hier steckst du deine Neo4j-Abfrage ein -- siehe `load_materials()`.
Der Rest der App ruft NUR `get_materials()` auf und weiß nichts von der
Herkunft der Daten. Dadurch bleibt der Neo4j-Wechsel eine Ein-Datei-Änderung.
"""
from __future__ import annotations

import polars as pl

# Spaltenreihenfolge wie in der Tabelle (Mockup)
COLUMNS = [
    "material_nr",
    "bezeichnung",
    "warengruppe",
    "werk",
    "status",
    "einheit",
    "bestand",
    "geaendert",
]

# Anzeigeüberschriften -> Spaltennamen (für DataTable)
COLUMN_LABELS = {
    "material_nr": "Material-Nr.",
    "bezeichnung": "Bezeichnung",
    "warengruppe": "Warengruppe",
    "werk": "Werk",
    "status": "Status",
    "einheit": "Einheit",
    "bestand": "Bestand",
    "geaendert": "Geändert",
}

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
    import random

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
    """Lädt die Materialdaten.

    >>> HIER später Neo4j einstecken. <<<
    Beispiel (mit dem offiziellen neo4j-Treiber):

        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(URI, auth=(USER, PW))
        cypher = '''
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
        '''
        with driver.session() as session:
            records = session.run(cypher).data()
        return pl.DataFrame(records, schema_overrides={"bestand": pl.Int64})

    Wichtig: Rückgabe muss dieselben Spalten (`COLUMNS`) liefern, dann
    funktioniert der Rest der App unverändert.
    """
    return _make_mock_frame()


# --------------------------------------------------------------------------
# Einfaches Caching: die Daten werden einmal pro Prozess geladen.
# Für Live-Neo4j-Daten kannst du hier eine TTL/Refresh-Logik einbauen
# oder `get_materials(force_reload=True)` aufrufen.
# --------------------------------------------------------------------------
_CACHE: pl.DataFrame | None = None


def get_materials(force_reload: bool = False) -> pl.DataFrame:
    global _CACHE
    if _CACHE is None or force_reload:
        _CACHE = load_materials()
    return _CACHE


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
