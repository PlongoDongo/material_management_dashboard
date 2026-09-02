"""
Test fixtures.

Two techniques carry the whole test suite:

1. **App factory.** `create_app(settings)` returns a fresh app with EXPLICIT
   configuration on every call -- no environment variables, no monkeypatching.
   The test controls the configuration directly.

2. **`dependency_overrides`.** FastAPI's built-in mechanism for replacing a
   dependency. We swap `get_sources` for a fake (tests/fakes.py). That runs the
   COMPLETE chain -- route, validation, product loader, transformation,
   envelope, cache, headers -- without any database having to exist. Only the
   bottom layer is replaced.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from data_api.api.deps import get_sources
from data_api.application import create_app
from data_api.core.config import Settings
from data_api.products.cache import cache
from tests.fakes import FakeSources


@pytest.fixture
def settings() -> Settings:
    return Settings(
        neo4j_uri=None,
        postgres_dsn=None,
        api_env="dev",
        api_keys=[],
        api_log_level="WARNING",
        _env_file=None,          # a developer's .env must not influence tests
    )


@pytest.fixture
def app(settings: Settings):
    """A fully wired app, but without a database."""
    cache.invalidate()           # test isolation: no cache bleed-through
    application = create_app(settings)
    application.dependency_overrides[get_sources] = FakeSources
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_sources(app):
    """THE one FakeSources instance the request uses.

    Without this fixture, `dependency_overrides[get_sources] = FakeSources`
    creates a new object per request and a test could never reach the recorded
    calls. Here one instance is pinned and returned, so a test can inspect
    `fake_sources.calls` afterwards.
    """
    fake = FakeSources()
    app.dependency_overrides[get_sources] = lambda: fake
    return fake


@pytest.fixture
def client_without_sources(settings: Settings):
    """An app WITHOUT the override -- shows what happens with no data source."""
    cache.invalidate()
    with TestClient(create_app(settings)) as test_client:
        yield test_client
