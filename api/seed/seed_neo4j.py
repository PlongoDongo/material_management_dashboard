#!/usr/bin/env python3
"""
Legt Mock-Daten in Neo4j an -- fuer Quellen, die noch nicht geliefert wurden.

    python seed/seed_neo4j.py
    python seed/seed_neo4j.py --purge

Jeder erzeugte Knoten traegt zusaetzlich das Label `:Mock`. Damit laesst sich
alles rueckstandsfrei entfernen, sobald die echten Daten da sind:

    MATCH (n:Mock) DETACH DELETE n

Das Skript nutzt bewusst den SYNCHRONEN Treiber: es ist ein einmaliges
Kommandozeilenwerkzeug, kein Server. Async waere hier nur Zeremonie.

Die erzeugte Struktur entspricht exakt dem, was
`src/data_api/repositories/materials.py` abfragt. Aendert sich dort das
Graphmodell, ist auch dieses Skript anzupassen.
"""
from __future__ import annotations

import argparse
import os
import random
import sys

from neo4j import GraphDatabase

WARENGRUPPEN = ["Betriebsstoffe", "Rohstoffe", "Fertigerzeugnisse", "Verpackung",
                "Ersatzteile", "Halbfabrikate", ""]      # "" = ohne Klassifizierung
WERKE = [("W-KOE", "Werk Koeln"), ("W-BER", "Werk Berlin"),
         ("W-MUC", "Werk Muenchen"), ("W-HAM", "Werk Hamburg")]
STATUS = ["Aktiv", "Nicht geliefert", "Obsolet", "Gesperrt"]
EINHEITEN = ["M", "KG", "L", "PAK", "ST"]
BEZEICHNUNGEN = ["Gewindestange M10", "Sensorhalter Typ B", "Dichtungsring NBR 25",
                 "Aluminiumprofil 40x40", "Steckverbinder 4-pol", "Oelfilter Standard",
                 "Edelstahlschraube M8x40", "Fuehrungsschiene 500mm", "Zahnriemen HTD-5M"]
LIEFERANTEN = [("L-001", "Nordstahl GmbH", "DE"), ("L-002", "Alpine Precision AG", "AT"),
               ("L-003", "Baltic Components OY", "FI"), ("L-004", "Iberia Metals SL", "ES")]

PURGE = "MATCH (n:Mock) DETACH DELETE n"

# UNWIND + MERGE statt 64 Einzelabfragen: eine Transaktion, ein Roundtrip.
# MERGE ist idempotent -- das Skript laesst sich gefahrlos mehrfach ausfuehren.
CREATE_MATERIALS = """
UNWIND $rows AS row
MERGE (m:Material:Mock {nr: row.nr})
  SET m.name = row.name, m.status = row.status, m.einheit = row.einheit,
      m.bestand = row.bestand, m.preis = row.preis, m.geaendert = row.geaendert
MERGE (werk:Werk:Mock {id: row.werk_id})
  SET werk.name = row.werk_name
MERGE (m)-[:LOCATED_IN]->(werk)
WITH m, row WHERE row.warengruppe <> ''
MERGE (w:Warengruppe:Mock {name: row.warengruppe})
MERGE (m)-[:HAS_WARENGRUPPE]->(w)
"""

CREATE_SUPPLIERS = """
UNWIND $rows AS row
MERGE (s:Lieferant:Mock {id: row.id})
  SET s.name = row.name, s.land = row.land
WITH s, row
UNWIND row.materialien AS material_nr
MATCH (m:Material {nr: material_nr})
MERGE (s)-[:SUPPLIES]->(m)
"""


def build_rows(n: int = 64) -> tuple[list[dict], list[dict]]:
    """Deterministisch (fester Seed) -- zweimal ausfuehren gibt dieselben Daten."""
    rng = random.Random(42)
    materials = []
    for i in range(n):
        werk_id, werk_name = rng.choice(WERKE)
        materials.append({
            "nr": f"MAT-{100777 + i * 13}",
            "name": rng.choice(BEZEICHNUNGEN),
            "warengruppe": rng.choices(WARENGRUPPEN, weights=[18, 18, 18, 12, 12, 16, 6])[0],
            "werk_id": werk_id,
            "werk_name": werk_name,
            "status": rng.choices(STATUS, weights=[55, 18, 15, 12])[0],
            "einheit": rng.choice(EINHEITEN),
            "bestand": rng.randint(300, 9800),
            "preis": round(rng.uniform(0.5, 480.0), 2),
            "geaendert": f"2026-{rng.randint(1, 8):02d}-{rng.randint(1, 28):02d}",
        })

    nummern = [m["nr"] for m in materials]
    suppliers = [
        {"id": lid, "name": name, "land": land,
         "materialien": rng.sample(nummern, rng.randint(8, 30))}
        for lid, name, land in LIEFERANTEN
    ]
    return materials, suppliers


def coerce_auth(auth: str | None) -> tuple[str, str] | None:
    """Gleiche Konvention wie die API: 'user/passwort' oder 'user:passwort'."""
    if auth and ("/" in auth or ":" in auth):
        sep = "/" if "/" in auth else ":"
        user, _, password = auth.partition(sep)
        return (user, password)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--purge", action="store_true",
                        help="Nur alle :Mock-Knoten loeschen, nichts anlegen.")
    parser.add_argument("--count", type=int, default=64, help="Anzahl Materialien.")
    args = parser.parse_args()

    uri = os.getenv("NEO4J_URI")
    if not uri:
        print("NEO4J_URI ist nicht gesetzt.", file=sys.stderr)
        return 2

    driver = GraphDatabase.driver(uri, auth=coerce_auth(os.getenv("NEO4J_AUTH")))
    database = os.getenv("NEO4J_DB", "neo4j")
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            geloescht = session.run(PURGE).consume().counters.nodes_deleted
            print(f"{geloescht} Mock-Knoten geloescht.")
            if args.purge:
                return 0

            materials, suppliers = build_rows(args.count)
            session.run(CREATE_MATERIALS, rows=materials)
            session.run(CREATE_SUPPLIERS, rows=suppliers)
            print(f"{len(materials)} Materialien und {len(suppliers)} Lieferanten angelegt.")

            angelegt = session.run(
                "MATCH (n:Mock) RETURN labels(n)[0] AS label, count(*) AS anzahl ORDER BY label"
            ).data()
            for row in angelegt:
                print(f"  {row['label']:<14} {row['anzahl']}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
