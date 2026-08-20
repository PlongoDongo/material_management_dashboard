"""
Test-Doubles fuer die Datenschicht.

Hier liegen die Beispieldaten -- NICHT in src/. Der Unterschied ist wichtig:

  src/    wird ausgeliefert. Ein Ersatzdatensatz im Produktionspfad kann in
          Produktion aktiv werden und erfundene Zahlen an ein Dashboard
          liefern. Deshalb gibt es dort keinen.
  tests/  wird nie ausgeliefert. Diese Doubles bleiben dauerhaft bestehen,
          weil Tests dauerhaft ohne laufende Datenbank auskommen muessen --
          in CI, im Zug, waehrend die DB migriert wird.

Eingehaengt werden sie ueber `dependency_overrides` (siehe conftest.py). Das
ist FastAPIs vorgesehener Mechanismus dafuer: die App weiss nichts davon, es
wird nichts gepatcht, und der Rest der Kette (Route, Validierung,
Transformation, Umschlag, Cache) laeuft unveraendert.

Die Zeilenform entspricht EXAKT dem, was der Cypher bzw. das SQL in
src/data_api/repositories/ zurueckgibt. Weicht sie ab, testet man am Ende die
Doubles statt der API.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Any

# Fester Seed -> reproduzierbare Daten, damit Tests exakte Werte pruefen koennen.
_WARENGRUPPEN = ["Betriebsstoffe", "Rohstoffe", "Fertigerzeugnisse", "Verpackung",
                 "Ersatzteile", "Halbfabrikate", ""]
_WERKE = [("W-KOE", "Werk Koeln"), ("W-BER", "Werk Berlin"),
          ("W-MUC", "Werk Muenchen"), ("W-HAM", "Werk Hamburg")]
_STATUS = ["Aktiv", "Nicht geliefert", "Obsolet", "Gesperrt"]
_EINHEITEN = ["M", "KG", "L", "PAK", "ST"]
_BEZEICHNUNGEN = ["Gewindestange M10", "Sensorhalter Typ B", "Dichtungsring NBR 25",
                  "Aluminiumprofil 40x40", "Steckverbinder 4-pol", "Oelfilter Standard",
                  "Edelstahlschraube M8x40", "Fuehrungsschiene 500mm", "Zahnriemen HTD-5M"]
_LIEFERANTEN = [("L-001", "Nordstahl GmbH", "DE"), ("L-002", "Alpine Precision AG", "AT"),
                ("L-003", "Baltic Components OY", "FI"), ("L-004", "Iberia Metals SL", "ES")]


def material_rows(n: int = 64) -> list[dict[str, Any]]:
    """Wie `_MATERIALS_CYPHER` in repositories/materials.py."""
    rng = random.Random(42)
    rows: list[dict[str, Any]] = []
    for i in range(n):
        werk_id, werk_name = rng.choice(_WERKE)
        rows.append({
            "material_nr": f"MAT-{100777 + i * 13}",
            "bezeichnung": rng.choice(_BEZEICHNUNGEN),
            "warengruppe": rng.choices(_WARENGRUPPEN, weights=[18, 18, 18, 12, 12, 16, 6])[0],
            "werk_id": werk_id,
            "werk_name": werk_name,
            "status": rng.choices(_STATUS, weights=[55, 18, 15, 12])[0],
            "einheit": rng.choice(_EINHEITEN),
            "bestand": rng.randint(300, 9800),
            "preis": round(rng.uniform(0.5, 480.0), 2),
            "geaendert": f"2026-{rng.randint(1, 8):02d}-{rng.randint(1, 28):02d}",
        })
    return rows


def supplier_rows() -> list[dict[str, Any]]:
    """Wie `_SUPPLIER_LINK_CYPHER` in repositories/materials.py."""
    rng = random.Random(7)
    return [
        {"lieferant_id": lid, "lieferant_name": name, "land": land,
         "anzahl_materialien": rng.randint(3, 40)}
        for lid, name, land in _LIEFERANTEN
    ]


def delivery_rows(seit: dt.date) -> list[dict[str, Any]]:
    """Wie `_DELIVERIES_SQL` in repositories/deliveries.py.

    Absichtlich unterschiedliche Zuverlaessigkeit je Lieferant, damit der
    Risiko-Score im Datenprodukt sichtbar streut.
    """
    rng = random.Random(11)
    rows: list[dict[str, Any]] = []
    for lieferant, verzug_bias in (("L-001", 0), ("L-002", 2), ("L-003", 6), ("L-004", 1)):
        for i in range(40):
            zugesagt = dt.date(2026, 1, 1) + dt.timedelta(days=i * 5)
            geliefert = zugesagt + dt.timedelta(days=max(0, int(rng.gauss(verzug_bias, 3))))
            if geliefert < seit:
                continue
            rows.append({
                "lieferant_id": lieferant,
                "material_nr": f"MAT-{100777 + rng.randint(0, 63) * 13}",
                "geliefert_am": geliefert,
                "zugesagt_am": zugesagt,
                "menge": rng.randint(10, 900),
                "reklamationen": rng.choices([0, 1, 2], weights=[85, 12, 3])[0],
            })
    return rows


class FakeMaterialsRepository:
    """Erfuellt das Protocol `MaterialsRepository` -- ohne Neo4j."""

    source = "fake"

    async def fetch_materials(self) -> list[dict[str, Any]]:
        return material_rows()

    async def fetch_suppliers(self) -> list[dict[str, Any]]:
        return supplier_rows()


class FakeDeliveriesRepository:
    """Erfuellt das Protocol `DeliveriesRepository` -- ohne Postgres."""

    source = "fake"

    async def fetch_deliveries(self, seit: dt.date) -> list[dict[str, Any]]:
        return delivery_rows(seit)


class FakeRepositories:
    """Tritt an die Stelle des echten `Repositories`-Containers.

    Kein AsyncExitStack, keine Sessions, keine Treiber -- die Datenprodukte
    merken davon nichts, weil sie ohnehin nur `await repos.materials()` kennen.
    """

    def __init__(self) -> None:
        self.sources_used: set[str] = set()

    async def materials(self) -> FakeMaterialsRepository:
        self.sources_used.add("fake")
        return FakeMaterialsRepository()

    async def deliveries(self) -> FakeDeliveriesRepository:
        self.sources_used.add("fake")
        return FakeDeliveriesRepository()

    @property
    def source_label(self) -> str:
        return "+".join(sorted(self.sources_used)) or "none"
