"""
Integration tests against a REAL Neo4j instance.

Why this file exists: as soon as a filter lives in the Cypher query rather than
in `transform()`, no test without a database can check that it filters
correctly. A fake would have to reimplement Cypher in Python -- and then the
test checks the fake, not the query.

    Without a database:  every test here is SKIPPED.
    With a database:     set NEO4J_URI + NEO4J_AUTH, run seed/seed_neo4j.py first.

        export NEO4J_URI=bolt://localhost:7687
        export NEO4J_AUTH=neo4j/password
        python seed/seed_neo4j.py
        pytest tests/test_integration_neo4j.py -v

These tests deliberately do NOT run in the normal suite, so the build stays
green without infrastructure.
"""
from __future__ import annotations

import os
from contextlib import AsyncExitStack

import pytest
import pytest_asyncio

from data_api.core.config import Settings
from data_api.db.neo4j import create_driver
from data_api.db.sources import Sources
from data_api.products.catalog import supplier_risk_v2 as sr2

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="NEO4J_URI is not set -- integration tests skipped.",
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def driver():
    """ONE driver for the whole session -- the same rule as in the server.

    Building one per test would mean one connection setup including
    verify_connectivity() per test. `try/finally` is mandatory: if a test fails,
    pytest throws the exception into the generator, and a bare `await
    driver.close()` after the yield would never be reached -- the connection
    pool would stay open. That is exactly the promise db/sources.py makes.
    """
    settings = Settings()
    neo4j_driver = await create_driver(settings.neo4j_uri, settings.neo4j_auth)
    try:
        yield neo4j_driver
    finally:
        await neo4j_driver.close()


@pytest_asyncio.fixture(loop_scope="session")
async def sources(driver):
    """One Sources object per test -- short-lived, as in a request."""
    async with AsyncExitStack() as stack:
        yield Sources(stack=stack, settings=Settings(),
                      neo4j_driver=driver, sql_sessionmaker=None)


async def test_the_query_returns_the_expected_columns(sources):
    """The contract between Cypher and transform(): which columns arrive?"""
    rows = await sources.neo4j(sr2.CYPHER, country=None)
    assert rows, "no suppliers found -- did you run seed/seed_neo4j.py?"
    assert set(rows[0]) == {"supplier_id", "supplier_name", "country", "material_count"}


async def test_the_optional_filter_drops_out_without_a_value(sources):
    """`$country IS NULL OR ...` -- with no value nothing is filtered."""
    everything = await sources.neo4j(sr2.CYPHER, country=None)
    assert len(everything) >= 2


async def test_the_optional_filter_applies_with_a_value(sources):
    """This is the test a fake cannot provide."""
    everything = await sources.neo4j(sr2.CYPHER, country=None)
    countries = sorted({row["country"] for row in everything if row["country"]})
    assert len(countries) >= 2, "the seed data should contain several countries."

    filtered = await sources.neo4j(sr2.CYPHER, country=[countries[0]])
    assert filtered, "the filter must not discard everything."
    assert {row["country"] for row in filtered} == {countries[0]}
    assert len(filtered) < len(everything)


async def test_neo4j_types_arrive_as_python_types(sources):
    """Checks the conversion in db/sources.py against real data."""
    import datetime as dt

    rows = await sources.neo4j(
        "RETURN date('2026-08-20') AS d, duration({days: 2}) AS dur, "
        "point({x: 1.0, y: 2.0}) AS location"
    )
    row = rows[0]
    assert row["d"] == dt.date(2026, 8, 20)
    assert row["dur"] == "P2D"
    assert row["location"]["x"] == 1.0 and "srid" in row["location"]
