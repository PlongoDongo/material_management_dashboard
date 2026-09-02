#!/usr/bin/env python3
"""
Creates mock data in Neo4j -- for sources that have not been delivered yet.

    python seed/seed_neo4j.py
    python seed/seed_neo4j.py --purge

Every node created also carries the `:Mock` label, so everything can be removed
without a trace once the real data arrives:

    MATCH (n:Mock) DETACH DELETE n

The script deliberately uses the SYNCHRONOUS driver: it is a one-off command
line tool, not a server. Async would be ceremony here.

The structure matches exactly what the catalog queries in
`src/data_api/products/catalog/` expect. If the graph model changes there, this
script has to change too.

The node labels and property names are German because they mirror the graph
model, not the code.
"""
from __future__ import annotations

import argparse
import os
import random
import sys

from neo4j import GraphDatabase

MATERIAL_GROUPS = ["Betriebsstoffe", "Rohstoffe", "Fertigerzeugnisse", "Verpackung",
                "Ersatzteile", "Halbfabrikate", ""]      # "" = unclassified
PLANTS = [("W-KOE", "Werk Koeln"), ("W-BER", "Werk Berlin"),
         ("W-MUC", "Werk Muenchen"), ("W-HAM", "Werk Hamburg")]
STATUSES = ["Aktiv", "Nicht geliefert", "Obsolet", "Gesperrt"]
UNITS = ["M", "KG", "L", "PAK", "ST"]
DESCRIPTIONS = ["Gewindestange M10", "Sensorhalter Typ B", "Dichtungsring NBR 25",
                 "Aluminiumprofil 40x40", "Steckverbinder 4-pol", "Oelfilter Standard",
                 "Edelstahlschraube M8x40", "Fuehrungsschiene 500mm", "Zahnriemen HTD-5M"]
SUPPLIERS = [("L-001", "Nordstahl GmbH", "DE"), ("L-002", "Alpine Precision AG", "AT"),
               ("L-003", "Baltic Components OY", "FI"), ("L-004", "Iberia Metals SL", "ES")]

PURGE = "MATCH (n:Mock) DETACH DELETE n"

# UNWIND + MERGE instead of 64 individual queries: one transaction, one round
# trip. MERGE is idempotent -- the script can safely be run more than once.
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
    """Deterministic (fixed seed) -- running it twice produces the same data."""
    rng = random.Random(42)
    materials = []
    for i in range(n):
        plant_id, plant_name = rng.choice(PLANTS)
        materials.append({
            "nr": f"MAT-{100777 + i * 13}",
            "name": rng.choice(DESCRIPTIONS),
            "warengruppe": rng.choices(MATERIAL_GROUPS, weights=[18, 18, 18, 12, 12, 16, 6])[0],
            "werk_id": plant_id,
            "werk_name": plant_name,
            "status": rng.choices(STATUSES, weights=[55, 18, 15, 12])[0],
            "einheit": rng.choice(UNITS),
            "bestand": rng.randint(300, 9800),
            "preis": round(rng.uniform(0.5, 480.0), 2),
            "geaendert": f"2026-{rng.randint(1, 8):02d}-{rng.randint(1, 28):02d}",
        })

    numbers = [m["nr"] for m in materials]
    suppliers = [
        {"id": sid, "name": name, "land": country,
         "materialien": rng.sample(numbers, rng.randint(8, 30))}
        for sid, name, country in SUPPLIERS
    ]
    return materials, suppliers


def coerce_auth(auth: str | None) -> tuple[str, str] | None:
    """Same convention as the API: 'user/password' or 'user:password'."""
    if auth and ("/" in auth or ":" in auth):
        sep = "/" if "/" in auth else ":"
        user, _, password = auth.partition(sep)
        return (user, password)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--purge", action="store_true",
                        help="Only delete all :Mock nodes, create nothing.")
    parser.add_argument("--count", type=int, default=64, help="Number of materials.")
    args = parser.parse_args()

    uri = os.getenv("NEO4J_URI")
    if not uri:
        print("NEO4J_URI is not set.", file=sys.stderr)
        return 2

    driver = GraphDatabase.driver(uri, auth=coerce_auth(os.getenv("NEO4J_AUTH")))
    database = os.getenv("NEO4J_DB", "neo4j")
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            deleted = session.run(PURGE).consume().counters.nodes_deleted
            print(f"{deleted} mock nodes deleted.")
            if args.purge:
                return 0

            materials, suppliers = build_rows(args.count)
            session.run(CREATE_MATERIALS, rows=materials)
            session.run(CREATE_SUPPLIERS, rows=suppliers)
            print(f"Created {len(materials)} materials and {len(suppliers)} suppliers.")

            created = session.run(
                "MATCH (n:Mock) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY label"
            ).data()
            for row in created:
                print(f"  {row['label']:<14} {row['count']}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
