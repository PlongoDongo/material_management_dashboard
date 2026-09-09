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

import time
from collections.abc import Iterable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fakes import FakeSources
from tests.types import AuthHeader, KeyPair, MakeToken

from data_api.api.deps import get_sources
from data_api.application import create_app
from data_api.core.config import Settings
from data_api.products.cache import cache


@pytest.fixture(autouse=True)
def _no_mounted_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:  # noqa: ANN001
    """Points CREDENTIALS_DIR at an empty directory for EVERY test.

    Without this the suite would read whatever the developer's machine has
    mounted at /etc/credentials -- passing locally and failing in CI, or worse,
    quietly talking to a real database. `_env_file=None` already does the same
    job for the .env file; this is the same isolation for the other source.
    """
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path_factory.mktemp("no-credentials")))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        neo4j_host=None,         # no graph -> FakeSources stands in
        sql_host=None,
        api_env="dev",
        oidc_issuer=None,        # auth off -- the default for most tests
        server_loglevel="warning",
        _env_file=None,          # a developer's .env must not influence tests
    )


# --- OIDC ------------------------------------------------------------------
# Auth is tested against REAL RS256 tokens, not by overriding current_principal:
# signature, exp, iss and aud are exactly the checks worth having a test for,
# and an override would skip all four. Only the JWKS lookup is replaced -- the
# one part that would need a Keycloak on the network.

ISSUER = "https://keycloak.test/realms/airbus"
AUDIENCE = "data-api"


@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[Any, Any]:
    """One throwaway RSA key for the whole session (generating it is slow)."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def oidc_settings(settings: Settings, rsa_keypair: KeyPair, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with auth ON, and the realm's public key wired in locally."""
    from data_api.core import security

    _, public = rsa_keypair
    monkeypatch.setattr(security, "_signing_key", lambda token, settings: public)
    return settings.model_copy(update={
        "oidc_issuer": ISSUER,
        "oidc_audience": AUDIENCE,
        "oidc_client_id": AUDIENCE,
    })


@pytest.fixture
def make_token(rsa_keypair: KeyPair) -> MakeToken:
    """Mints a Keycloak-shaped access token. Every claim can be overridden."""
    import jwt

    private, _ = rsa_keypair

    def _make(
        *,
        roles: Iterable[str] = (),
        groups: Iterable[str] = (),
        username: str = "m.renner",
        expires_in: int = 300,
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
        # ANN401: any JWT claim may be overridden by a test.
        **claims: Any,  # noqa: ANN401
    ) -> str:
        now = int(time.time())
        payload = {
            "sub": "0f2c1e5a-1111-2222-3333-444455556666",
            "preferred_username": username,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
            "realm_access": {"roles": list(roles)},
            "resource_access": {AUDIENCE: {"roles": []}},
            "groups": list(groups),
            **claims,
        }
        return jwt.encode(payload, private, algorithm="RS256")

    return _make


@pytest.fixture
def auth_header(make_token: MakeToken) -> AuthHeader:
    """`client.get(path, headers=auth_header(roles=["planner"]))`."""
    def _header(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401 -- forwarded to make_token
        return {"Authorization": f"Bearer {make_token(**kwargs)}"}
    return _header


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """A fully wired app, but without a database."""
    cache.invalidate()           # test isolation: no cache bleed-through
    application = create_app(settings)
    application.dependency_overrides[get_sources] = FakeSources
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_sources(app: FastAPI) -> FakeSources:
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
def client_without_sources(settings: Settings) -> Iterator[TestClient]:
    """An app WITHOUT the override -- shows what happens with no data source."""
    cache.invalidate()
    with TestClient(create_app(settings)) as test_client:
        yield test_client
