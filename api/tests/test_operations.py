"""
Tests of the operational layer.

This file covers bugs that all shared one pattern: none of them raised an
exception, they just answered a different question than the one asked. Exactly
the class of failure the rest of the project argues against (`extra="forbid"`,
the type conversion, the LIMIT section in the guide) -- the operational layer
had simply not been held to the same standard yet.
"""
from __future__ import annotations

import locale
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fakes import FakeSources
from tests.types import AuthHeader

from api.deps import get_sources
from app import create_app
from core.config import Settings
from core.errors import ConflictError
from core.security import ANONYMOUS, Principal
from products.cache import cache

# --- .env.example is a shipped interface ------------------------------------

def test_env_example_loads_and_leaves_auth_off(tmp_path: Path) -> None:
    """`cp .env.example .env` must produce an app that starts.

    Two traps at once: pydantic-settings parses complex fields (list[str]) as
    JSON inside the source -- without NoDecode the app fails to start on
    `API_CORS_ORIGINS=a,b`. And python-dotenv only strips a trailing comment
    when a value precedes it: `OIDC_ISSUER=  # empty = off` would have read the
    comment text as an issuer and switched auth ON.
    """

    example = Path(__file__).resolve().parents[1] / ".env.example"
    target = tmp_path / ".env"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=str(target))
    assert settings.cors_origins == ["http://localhost:8050", "http://localhost:8051"]
    assert not settings.oidc_issuer
    assert settings.auth_enabled is False


# --- Request-ID -------------------------------------------------------------

def test_request_id_appears_in_the_access_log_line(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """The one line tying path, status and duration together needs the id.

    Previously the ContextVar was reset in `finally` -- that is, BEFORE the log
    call -- and that very line carried "-".
    """
    with caplog.at_level("INFO", logger="core.middleware"):
        client.get("/api/v1/healthz", headers={"X-Request-ID": "abc123"})

    lines = [r for r in caplog.records if "healthz" in r.getMessage()]
    assert lines, "no access log line found"
    assert lines[-1].request_id == "abc123"


def test_the_request_id_survives_a_server_error(settings: Settings) -> None:
    """On a 500 the response no longer passes through the middleware.

    That is exactly where correlation is worth the most -- so the id has to be
    in both the body and the header.
    """
    app = create_app(settings)

    @app.get("/boom")
    # `-> None`, not the `Never` a type checker would infer: FastAPI builds a
    # response model from the return annotation and cannot make one from Never.
    async def boom() -> None:
        raise RuntimeError("deliberate")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-ID": "abc123"})

    assert response.status_code == 500
    assert response.json()["request_id"] == "abc123"
    assert response.headers["X-Request-ID"] == "abc123"


# --- Envelope metadata ------------------------------------------------------

def test_meta_source_stays_correct_on_cache_hits(client: TestClient) -> None:
    """On a cache hit no query runs -- the source must still be right.

    Previously every cached response reported `source="none"`. With
    cache_ttl=300 on supplier-risk that was the majority of all responses.
    """
    path = "/api/v1/data-products/supplier-risk/v2"
    first = client.get(path).json()["meta"]
    second = client.get(path).json()["meta"]

    assert first["cache"] == "miss" and second["cache"] == "hit"
    assert second["source"] == first["source"] == "neo4j+postgres"


def test_generated_at_means_the_time_of_the_query(client: TestClient) -> None:
    """Not "now" -- otherwise the age field would look fresh on every hit."""
    path = "/api/v1/data-products/supplier-risk/v2"
    first = client.get(path).json()["meta"]["generated_at"]
    second = client.get(path).json()["meta"]["generated_at"]
    assert first == second


def test_paging_does_not_trigger_another_database_run(client: TestClient, fake_sources: FakeSources) -> None:
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

def test_the_sunset_header_is_locale_independent(client: TestClient) -> None:
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

def test_the_catalog_is_as_protected_as_the_data_products(oidc_settings: Settings, auth_header: AuthHeader) -> None:
    """The catalog lists cache times and every contract field.

    Leaving it open without a key would be a decision -- previously it was just
    an omitted line.
    """
    with TestClient(create_app(oidc_settings)) as client:
        assert client.get("/api/v1/catalog").status_code == 401
        assert client.get("/api/v1/catalog/material-overview").status_code == 401
        assert client.get("/api/v1/catalog", headers=auth_header()).status_code == 200


def test_disabled_auth_does_not_lock_anyone_out() -> None:
    """"Auth off" has to mean EVERYTHING is open, not "only the public group".

    Previously development was stricter than production: a product with
    required_groups=("internal",) answered 403 locally even with auth disabled.
    """
    assert ANONYMOUS.may_access(("internal",)) is True
    assert ANONYMOUS.may_access(()) is True

    authenticated = Principal(subject="x", groups=frozenset({"public"}), auth_enabled=True)
    assert authenticated.may_access(("internal",)) is False
    assert authenticated.may_access(("public",)) is True


def test_a_failed_commit_is_the_response_not_a_log_line(app: FastAPI, client: TestClient) -> None:
    """The commit runs after the handler has returned -- a duplicate key is
    often only detected there.

    With a dependency's default scope that code runs AFTER the response has
    left: the failure is only logged, and the client holds a 201 for data that
    was never written. `scope="function"` on SourcesDep moves it in front of
    the response. Without it this test sees 201.
    """
    async def sources_whose_commit_conflicts() -> AsyncIterator[FakeSources]:
        yield FakeSources()
        raise ConflictError("duplicate key value violates unique constraint")

    app.dependency_overrides[get_sources] = sources_whose_commit_conflicts
    key = cache.make_key("material-overview", 3, "{}")
    cache.set(key, (["row"], 1, "neo4j", None), ttl=60)

    response = client.post(
        "/api/v1/material-relationships",
        json={"material_rep_1_id": "11111111-1111-1111-1111-111111111111",
              "material_rep_2_id": "22222222-2222-2222-2222-222222222222"},
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "conflict"
    assert cache.get(key) is not None, "a write that failed must not evict the cache"

