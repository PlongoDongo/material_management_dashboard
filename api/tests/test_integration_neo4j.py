"""
Integrationstests gegen eine ECHTE Neo4j-Instanz.

Warum es diese Datei gibt: Sobald ein Filter in der Cypher-Abfrage steht statt
in `transform()`, kann kein Test ohne Datenbank mehr pruefen, ob er richtig
filtert. Ein Fake muesste dafuer Cypher in Python nachbauen -- dann testet man
den Fake, nicht die Abfrage.

    Ohne Datenbank:  alle Tests hier werden UEBERSPRUNGEN.
    Mit Datenbank:   NEO4J_URI + NEO4J_AUTH setzen, vorher seed/seed_neo4j.py
                     ausfuehren.

        export NEO4J_URI=bolt://localhost:7687
        export NEO4J_AUTH=neo4j/passwort
        python seed/seed_neo4j.py
        pytest tests/test_integration_neo4j.py -v

Diese Tests laufen bewusst NICHT in der normalen Suite mit, damit der Build
ohne Infrastruktur gruen bleibt.
"""
from __future__ import annotations

import os
from contextlib import AsyncExitStack

import pytest

from data_api.core.config import Settings
from data_api.db.neo4j import create_driver
from data_api.db.sources import Sources
from data_api.products.catalog import supplier_risk_v1 as sr1

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="NEO4J_URI nicht gesetzt -- Integrationstests uebersprungen.",
)


@pytest.fixture
async def sources():
    """Ein echtes Sources-Objekt gegen die konfigurierte Datenbank."""
    settings = Settings()
    driver = await create_driver(settings.neo4j_uri, settings.neo4j_auth)
    async with AsyncExitStack() as stack:
        yield Sources(stack=stack, settings=settings,
                      neo4j_driver=driver, sql_sessionmaker=None)
    await driver.close()


async def test_abfrage_liefert_die_erwarteten_spalten(sources):
    """Der Vertrag zwischen Cypher und transform(): welche Spalten kommen an?"""
    zeilen = await sources.neo4j(sr1.CYPHER, land=None)
    assert zeilen, "Keine Lieferanten gefunden -- seed/seed_neo4j.py ausgefuehrt?"
    assert set(zeilen[0]) == {"lieferant_id", "lieferant_name", "land", "anzahl_materialien"}


async def test_optionaler_filter_faellt_ohne_wert_weg(sources):
    """`$land IS NULL OR ...` -- ohne Wert wird nichts gefiltert."""
    alle = await sources.neo4j(sr1.CYPHER, land=None)
    assert len(alle) >= 2


async def test_optionaler_filter_greift_mit_wert(sources):
    """Das ist der Test, den ein Fake nicht leisten kann."""
    alle = await sources.neo4j(sr1.CYPHER, land=None)
    laender = sorted({z["land"] for z in alle if z["land"]})
    assert len(laender) >= 2, "Seed-Daten sollten mehrere Laender enthalten."

    gefiltert = await sources.neo4j(sr1.CYPHER, land=[laender[0]])
    assert gefiltert, "Filter darf nicht alles wegwerfen."
    assert {z["land"] for z in gefiltert} == {laender[0]}
    assert len(gefiltert) < len(alle)


async def test_neo4j_typen_kommen_als_python_typen_an(sources):
    """Prueft die Umwandlung aus db/sources.py gegen echte Daten."""
    import datetime as dt

    zeilen = await sources.neo4j(
        "RETURN date('2026-08-20') AS d, duration({days: 2}) AS dauer, "
        "point({x: 1.0, y: 2.0}) AS ort"
    )
    zeile = zeilen[0]
    assert zeile["d"] == dt.date(2026, 8, 20)
    assert zeile["dauer"] == "P2D"
    assert zeile["ort"]["x"] == 1.0 and "srid" in zeile["ort"]
