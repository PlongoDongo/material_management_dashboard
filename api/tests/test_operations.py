"""
Tests of the operational layer.

This file covers bugs that all shared one pattern: none of them raised an
exception, they just answered a different question than the one asked. Exactly
the class of failure the rest of the project argues against (`extra="forbid"`,
the type conversion, the LIMIT section in the guide) -- the operational layer
had simply not been held to the same standard yet.
"""
from __future__ import annotations

import datetime as dt
import locale

import pytest
from fastapi.testclient import TestClient

from data_api.application import create_app
from data_api.core.config import Settings
from data_api.core.security import ANONYMOUS, Principal


# --- .env.example is a shipped interface ------------------------------------

def test_env_example_loads_and_leaves_auth_off(tmp_path):
    """`cp .env.example .env` must produce an app that starts.

    Two traps at once: pydantic-settings parses complex fields (list[str]) as
    JSON inside the source -- without NoDecode the app fails to start on
    `API_CORS_ORIGINS=a,b`. And python-dotenv only strips a trailing comment
    when a value precedes it: `API_KEYS=  # empty = off` would have read the
    comment text as a key and switched auth ON.
    """
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    target = tmp_path / ".env"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=str(target))
    assert settings.api_cors_origins == ["http://localhost:8050", "http://localhost:8051"]
    assert settings.api_keys == []
    assert settings.auth_enabled is False


# --- Request-ID -------------------------------------------------------------

def test_request_id_appears_in_the_access_log_line(client, caplog):
    """The one line tying path, status and duration together needs the id.

    Previously the ContextVar was reset in `finally` -- that is, BEFORE the log
    call -- and that very line carried "-".
    """
    with caplog.at_level("INFO", logger="data_api.core.middleware"):
        client.get("/api/v1/healthz", headers={"X-Request-ID": "abc123"})

    lines = [r for r in caplog.records if "healthz" in r.getMessage()]
    assert lines, "no access log line found"
    assert lines[-1].request_id == "abc123"


def test_the_request_id_survives_a_server_error(settings):
    """On a 500 the response no longer passes through the middleware.

    That is exactly where correlation is worth the most -- so the id has to be
    in both the body and the header.
    """
    app = create_app(settings)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("deliberate")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-ID": "abc123"})

    assert response.status_code == 500
    assert response.json()["request_id"] == "abc123"
    assert response.headers["X-Request-ID"] == "abc123"


# --- Envelope metadata ------------------------------------------------------

def test_meta_source_stays_correct_on_cache_hits(client):
    """On a cache hit no query runs -- the source must still be right.

    Previously every cached response reported `source="none"`. With
    cache_ttl=300 on supplier-risk that was the majority of all responses.
    """
    path = "/api/v1/data-products/supplier-risk/v2"
    first = client.get(path).json()["meta"]
    second = client.get(path).json()["meta"]

    assert first["cache"] == "miss" and second["cache"] == "hit"
    assert second["source"] == first["source"] == "neo4j+postgres"


def test_generated_at_means_the_time_of_the_query(client):
    """Not "now" -- otherwise the age field would look fresh on every hit."""
    path = "/api/v1/data-products/supplier-risk/v2"
    first = client.get(path).json()["meta"]["generated_at"]
    second = client.get(path).json()["meta"]["generated_at"]
    assert first == second


def test_paging_does_not_trigger_another_database_run(client, fake_sources):
    """limit/offset select a window, they do not define the dataset.

    Previously they were part of the cache key: every page was a full re-run of
    the loader, and the same dataset sat in the cache N times.
    """
    path = "/api/v1/data-products/material-overview/v3"
    client.get(path, params={"limit": 20, "offset": 0})
    calls_after_page_1 = len(fake_sources.calls)

    for offset in (20, 40):
        response = client.get(path, params={"limit": 20, "offset": offset})
        assert response.json()["meta"]["cache"] == "hit"
        assert response.json()["meta"]["total_count"] == 64

    assert len(fake_sources.calls) == calls_after_page_1


# --- Sunset header ----------------------------------------------------------

def test_the_sunset_header_is_locale_independent(client):
    """RFC 9110 requires a fixed, English date format.

    `strftime("%a, %d %b ...")` follows the container locale and produced
    "Do., 31 Dez. 2026" under LANG=de_DE -- unparseable for any client.
    """
    try:
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    except locale.Error:
        pytest.skip("Locale de_DE.UTF-8 is not installed")
    try:
        response = client.get("/api/v1/data-products/material-overview/v2")
        assert response.headers["Sunset"] == "Thu, 31 Dec 2026 00:00:00 GMT"
    finally:
        locale.setlocale(locale.LC_TIME, "C")


# --- Authentication ---------------------------------------------------------

def test_the_catalog_is_as_protected_as_the_data_products(settings):
    """The catalog lists owners, cache times and every contract field.

    Leaving it open without a key would be a decision -- previously it was just
    an omitted line.
    """
    protected = settings.model_copy(update={"api_keys": ["secret"]})
    with TestClient(create_app(protected)) as client:
        assert client.get("/api/v1/catalog").status_code == 401
        assert client.get("/api/v1/catalog/material-overview").status_code == 401
        assert client.get("/api/v1/catalog",
                          headers={"X-API-Key": "secret"}).status_code == 200


def test_disabled_auth_does_not_lock_anyone_out():
    """"Auth off" has to mean EVERYTHING is open, not "only the public group".

    Previously development was stricter than production: a product with
    required_groups=("internal",) answered 403 locally even with auth disabled.
    """
    assert ANONYMOUS.may_access(("internal",)) is True
    assert ANONYMOUS.may_access(()) is True

    authenticated = Principal(subject="x", groups=frozenset({"public"}), auth_enabled=True)
    assert authenticated.may_access(("internal",)) is False
    assert authenticated.may_access(("public",)) is True


def test_the_api_key_does_not_appear_in_the_response(settings):
    """`changed_by` should hold an identity, not credentials.

    Previously the first four characters of the key ended up in the response and
    in every log line.
    """
    protected = settings.model_copy(update={"api_keys": ["very-long-secret"]})
    with TestClient(create_app(protected)) as client:
        response = client.post(
            "/api/v1/mappings",
            headers={"X-API-Key": "very-long-secret"},
            json={"material_number": "MAT-1", "target_material_group": "Rohstoffe"},
        )
    assert response.status_code == 201
    subject = response.json()["changed_by"]
    assert subject.startswith("apikey:")
    assert "very" not in subject           # no characters of the key itself


# --- Readiness --------------------------------------------------------------

def test_readyz_only_checks_required_sources(client_without_sources):
    """Both sources are needed here -> both are missing -> 503."""
    response = client_without_sources.get("/api/v1/readyz")
    assert response.status_code == 503
    assert set(response.json()["required"]) == {"neo4j", "postgres"}


def test_required_sources_are_read_from_the_loaders():
    """Derived, not declared -- that way it cannot drift."""
    from data_api.products.introspect import required_sources, sources_used_by
    from data_api.products.catalog.material_overview_v3 import load as load_material
    from data_api.products.catalog.supplier_risk_v2 import load as load_risk

    assert sources_used_by(load_material) == ["neo4j"]
    assert sources_used_by(load_risk) == ["neo4j", "postgres"]
    assert required_sources() == {"neo4j", "postgres"}
