"""
Test-Fixtures.

Zwei Techniken tragen die gesamte Testsuite:

1. **App-Fabrik.** `create_app(settings)` liefert bei jedem Aufruf eine frische
   App mit EXPLIZITER Konfiguration -- keine Umgebungsvariablen, kein
   Monkeypatching. Der Test kontrolliert die Konfiguration direkt.

2. **`dependency_overrides`.** FastAPIs eingebauter Mechanismus, um eine
   Dependency zu ersetzen. Wir tauschen `get_repositories` gegen einen Fake aus
   (tests/fakes.py). Damit laeuft die KOMPLETTE Kette -- Route, Validierung,
   Produkt-Loader, Transformation, Umschlag, Cache, Header -- ohne dass eine
   Datenbank existieren muss. Ersetzt wird nur die unterste Schicht.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from data_api.api.deps import get_repositories
from data_api.application import create_app
from data_api.core.config import Settings
from data_api.products.cache import cache
from tests.fakes import FakeRepositories


@pytest.fixture
def settings() -> Settings:
    return Settings(
        neo4j_uri=None,
        postgres_dsn=None,
        api_env="dev",
        api_keys=[],
        api_log_level="WARNING",
        _env_file=None,          # .env des Entwicklers darf Tests nicht beeinflussen
    )


@pytest.fixture
def app(settings: Settings):
    """App mit echter Verdrahtung, aber ohne Datenbank."""
    cache.invalidate()           # Testisolation: kein Cache-Uebersprung
    application = create_app(settings)
    application.dependency_overrides[get_repositories] = FakeRepositories
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_ohne_datenquellen(settings: Settings):
    """App OHNE Override -- zeigt, was ohne konfigurierte Datenquelle passiert."""
    cache.invalidate()
    with TestClient(create_app(settings)) as test_client:
        yield test_client
