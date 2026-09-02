"""
Test-Doubles fuer die Datenschicht.

Hier liegen die Beispieldaten -- NICHT in src/. Der Unterschied:

  src/    wird ausgeliefert. Ein Ersatzdatensatz im Produktionspfad kann in
          Produktion aktiv werden und erfundene Zahlen liefern. Es gibt keinen.
  tests/  wird nie ausgeliefert. Diese Doubles bleiben dauerhaft, weil Tests
          dauerhaft ohne laufende Datenbank auskommen muessen.

`FakeSources` tritt ueber `dependency_overrides` an die Stelle von `Sources`
(siehe conftest.py). Es bildet die ABFRAGE auf die Antwortzeilen ab -- die
Abfragen werden dazu direkt aus den Katalogdateien importiert, es gibt also
keine kopierten Zeichenketten, die auseinanderlaufen koennen.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Any

from data_api.products.catalog import material_overview_v1 as mo1
from data_api.products.catalog import material_overview_v2 as mo2
from data_api.products.catalog import supplier_risk_v1 as sr1

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


def _material_basis(n: int = 64) -> list[dict[str, Any]]:
    rng = random.Random(42)
    zeilen = []
    for i in range(n):
        werk_id, werk_name = rng.choice(_WERKE)
        zeilen.append({
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
    return zeilen


def material_rows_v1() -> list[dict[str, Any]]:
    """Wie mo1.CYPHER: mit `werk` und `einheit`, ohne werk_id/preis."""
    return [
        {k: v for k, v in zeile.items() if k not in ("werk_id", "werk_name", "preis")}
        | {"werk": zeile["werk_name"]}
        for zeile in _material_basis()
    ]


def material_rows_v2() -> list[dict[str, Any]]:
    """Wie mo2.CYPHER: mit werk_id/werk_name und preis, ohne einheit."""
    return [
        {k: v for k, v in zeile.items() if k != "einheit"}
        for zeile in _material_basis()
    ]


def supplier_rows() -> list[dict[str, Any]]:
    """Wie sr1.CYPHER."""
    rng = random.Random(7)
    return [
        {"lieferant_id": lid, "lieferant_name": name, "land": land,
         "anzahl_materialien": rng.randint(3, 40)}
        for lid, name, land in _LIEFERANTEN
    ]


def delivery_rows(seit: dt.date) -> list[dict[str, Any]]:
    """Wie sr1.SQL. Unterschiedliche Zuverlaessigkeit je Lieferant, damit der
    Risiko-Score im Datenprodukt sichtbar streut."""
    rng = random.Random(11)
    zeilen = []
    for lieferant, verzug_bias in (("L-001", 0), ("L-002", 2), ("L-003", 6), ("L-004", 1)):
        for i in range(40):
            zugesagt = dt.date(2026, 1, 1) + dt.timedelta(days=i * 5)
            geliefert = zugesagt + dt.timedelta(days=max(0, int(rng.gauss(verzug_bias, 3))))
            if geliefert < seit:
                continue
            zeilen.append({
                "lieferant_id": lieferant,
                "material_nr": f"MAT-{100777 + rng.randint(0, 63) * 13}",
                "geliefert_am": geliefert,
                "zugesagt_am": zugesagt,
                "menge": rng.randint(10, 900),
                "reklamationen": rng.choices([0, 1, 2], weights=[85, 12, 3])[0],
            })
    return zeilen


class FakeSources:
    """Tritt an die Stelle von `Sources` -- ohne Treiber, ohne Sessions.

    Die Datenprodukte merken davon nichts: sie rufen `await sources.neo4j(...)`
    und bekommen Zeilen zurueck, genau wie im Ernstfall.
    """

    def __init__(self) -> None:
        self.used: set[str] = set()
        self.abfragen: list[str] = []          # fuer Tests, die pruefen WAS gefragt wurde

    async def neo4j(self, cypher: str, **parameter: Any) -> list[dict[str, Any]]:
        self.used.add("neo4j")
        self.abfragen.append(cypher)
        if cypher is mo1.CYPHER:
            return material_rows_v1()
        if cypher is mo2.CYPHER:
            return material_rows_v2()
        if cypher is sr1.CYPHER:
            return supplier_rows()
        raise AssertionError(
            "FakeSources kennt diese Cypher-Abfrage nicht. Neues Datenprodukt? "
            "Dann in tests/fakes.py eine passende Antwort ergaenzen.\n\n" + cypher
        )

    async def postgres(self, sql: str, **parameter: Any) -> list[dict[str, Any]]:
        self.used.add("postgres")
        self.abfragen.append(sql)
        if sql is sr1.SQL:
            return delivery_rows(parameter["seit"])
        raise AssertionError(
            "FakeSources kennt diese SQL-Abfrage nicht. Neues Datenprodukt? "
            "Dann in tests/fakes.py eine passende Antwort ergaenzen.\n\n" + sql
        )

    @property
    def label(self) -> str:
        return "+".join(sorted(self.used)) or "none"
