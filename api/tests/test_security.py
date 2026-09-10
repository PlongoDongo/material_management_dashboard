"""
Authentication and authorisation against REAL RS256 tokens.

The tokens here are signed with a throwaway key from conftest.py, and only the
JWKS lookup is replaced. Everything the module actually protects -- signature,
`exp`, `iss`, `aud`, role extraction -- runs for real. Overriding
`current_principal` instead would make every test below pass on a broken
implementation, which is the one thing an auth test must not do.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.types import AuthHeader, MakeToken

from data_api.app import create_app
from data_api.core.config import Settings
from data_api.core.security import ANONYMOUS, Principal, _groups_from
from data_api.products.registry import registry

CATALOG = "/api/v1/catalog"
PRODUCT = "/api/v1/data-products/material-overview/v3"


@pytest.fixture
def secured(oidc_settings: Settings, app: FastAPI) -> Iterator[TestClient]:
    """The normal test app, but with authentication switched on.

    Reuses the `app` fixture's FakeSources override so these tests stay about
    auth and do not need a database.
    """
    from tests.fakes import FakeSources

    from data_api.api.deps import get_sources

    application = create_app(oidc_settings)
    application.dependency_overrides[get_sources] = FakeSources
    with TestClient(application) as client:
        yield client


# --- The token has to be valid ---------------------------------------------

def test_a_valid_token_gets_through(secured: TestClient, auth_header: AuthHeader) -> None:
    assert secured.get(PRODUCT, headers=auth_header()).status_code == 200


def test_no_header_is_401_not_403(secured: TestClient) -> None:
    """401 tells the dashboard "log in again", 403 tells it "you lack a role".

    Collapsing the two would send a user with an expired session to a "no
    permission" page they can do nothing about.
    """
    response = secured.get(PRODUCT)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


@pytest.mark.parametrize("header", [
    {"Authorization": "Basic abc"},                 # wrong scheme
    {"Authorization": "Bearer"},                    # no token
    {"Authorization": "Bearer not-a-jwt"},          # not decodable
    {"Authorization": ""},                          # empty
])
def test_malformed_authorization_headers_are_rejected(secured: TestClient, header: dict[str, str]) -> None:
    assert secured.get(PRODUCT, headers=header).status_code == 401


def test_an_expired_token_is_rejected(secured: TestClient, auth_header: AuthHeader) -> None:
    assert secured.get(PRODUCT, headers=auth_header(expires_in=-60)).status_code == 401


def test_a_token_from_another_realm_is_rejected(secured: TestClient, auth_header: AuthHeader) -> None:
    """Right signature, wrong issuer. Without the `iss` check any realm this
    key serves could mint valid tokens for us."""
    other = auth_header(issuer="https://keycloak.test/realms/somewhere-else")
    assert secured.get(PRODUCT, headers=other).status_code == 401


def test_a_token_for_another_client_is_rejected(secured: TestClient, auth_header: AuthHeader) -> None:
    """The `aud` check. A user legitimately logged into a DIFFERENT application
    in the same realm holds a perfectly valid token -- it just is not for us."""
    assert secured.get(PRODUCT, headers=auth_header(audience="other-app")).status_code == 401


def test_a_tampered_token_is_rejected(secured: TestClient, make_token: MakeToken) -> None:
    """Flipping a character breaks the signature, which is the whole point."""
    token = make_token(roles=["planner"])
    head, payload, signature = token.split(".")
    forged = f"{head}.{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
    response = secured.get(PRODUCT, headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_the_rejection_reason_does_not_reach_the_caller(secured: TestClient, auth_header: AuthHeader) -> None:
    """"Signature has expired" vs "Invalid audience" is a hint we owe an
    operator in the log, not an anonymous caller in the response body."""
    body = secured.get(PRODUCT, headers=auth_header(expires_in=-60)).json()
    assert body["detail"] == "The token is invalid or has expired."
    for leak in ("expired", "audience", "signature", "iss"):
        assert leak not in body["detail"].lower().replace("expired.", "")


# --- Claims -> Principal ----------------------------------------------------

def test_realm_client_and_group_claims_all_become_groups(oidc_settings: Settings) -> None:
    """Which of the three a realm uses is a configuration choice, so a data
    product must not have to care. Group paths lose their leading slash."""
    claims = {
        "realm_access": {"roles": ["realm-role"]},
        "resource_access": {"data-api": {"roles": ["client-role"]}},
        "groups": ["/planners", "buyers"],
    }
    assert _groups_from(claims, oidc_settings) == frozenset(
        {"realm-role", "client-role", "planners", "buyers"}
    )


def test_missing_claims_do_not_crash(oidc_settings: Settings) -> None:
    """Keycloak omits these blocks entirely when a realm has no mappers set up.
    An auth layer that raises on a well-formed token is an outage."""
    assert _groups_from({}, oidc_settings) == frozenset()
    assert _groups_from({"realm_access": {}, "groups": None}, oidc_settings) == frozenset()


def test_the_username_becomes_the_readable_label(secured: TestClient, auth_header: AuthHeader) -> None:
    response = secured.post(
        "/api/v1/mappings",
        headers=auth_header(username="a.schmidt", roles=["material-planner"]),
        json={"material_number": "MAT-1", "target_material_group": "Rohstoffe"},
    )
    assert response.json()["changed_by"] == "a.schmidt"


def test_a_service_account_without_a_username_falls_back_to_the_subject() -> None:
    principal = Principal(subject="uuid-1", username="")
    assert principal.label == "uuid-1"


# --- Authorisation ----------------------------------------------------------

def test_disabled_auth_does_not_lock_anyone_out() -> None:
    """"Auth off" has to mean EVERYTHING is open, not "only the public group".

    Otherwise development is stricter than production: a product with
    required_groups=("internal",) would answer 403 locally even with auth off.
    """
    assert ANONYMOUS.may_access(("internal",)) is True
    assert ANONYMOUS.may_access(()) is True

    authenticated = Principal(subject="x", groups=frozenset({"public"}), auth_enabled=True)
    assert authenticated.may_access(("internal",)) is False
    assert authenticated.may_access(("public",)) is True


@pytest.fixture
def restricted_registry() -> Iterator[None]:
    """Puts required_groups on material-overview/v3 for the duration of a test.

    The shipped catalog has everything open, so authorisation would otherwise
    have nothing to bite on. `DataProduct` is frozen, so the entry is swapped
    for a modified copy rather than mutated -- and swapped back afterwards,
    because the registry is process-wide and would leak into the next test.
    """
    import dataclasses

    # Every major, not just v3: the catalog lists a product as long as ONE
    # version is visible, so leaving v2 open would keep the name in the list
    # and make the test below pass for the wrong reason.
    originals = {key: product for key, product in registry._products.items()
                 if key[0] == "material-overview"}
    for key, product in originals.items():
        registry._products[key] = dataclasses.replace(product, required_groups=("planner",))
    yield
    registry._products.update(originals)


@pytest.fixture
def secured_restricted(restricted_registry: None, oidc_settings: Settings) -> Iterator[TestClient]:
    """A secured client whose app was built AFTER the restriction was applied.

    Order matters and is easy to get wrong: `_make_endpoint` closes over the
    product object at route-creation time, so restricting the registry after
    `create_app` would change the catalog but not the route.
    """
    from tests.fakes import FakeSources

    from data_api.api.deps import get_sources

    application = create_app(oidc_settings)
    application.dependency_overrides[get_sources] = FakeSources
    with TestClient(application) as client:
        yield client


def test_a_missing_role_is_403_with_a_code(secured_restricted: TestClient, auth_header: AuthHeader) -> None:
    response = secured_restricted.get(PRODUCT, headers=auth_header(roles=["viewer"]))
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_the_matching_role_gets_through(secured_restricted: TestClient, auth_header: AuthHeader) -> None:
    assert secured_restricted.get(PRODUCT, headers=auth_header(roles=["planner"])).status_code == 200


def test_one_matching_role_out_of_several_is_enough(secured_restricted: TestClient, auth_header: AuthHeader) -> None:
    header = auth_header(roles=["viewer", "planner", "auditor"])
    assert secured_restricted.get(PRODUCT, headers=header).status_code == 200


# --- The catalog only lists what the caller may fetch ------------------------

def test_the_catalog_hides_products_the_caller_may_not_fetch(secured_restricted: TestClient, auth_header: AuthHeader) -> None:
    """The dashboard builds its menu from this list. Listing a product that
    answers 403 when clicked is worse than not listing it at all."""
    names = [entry["name"] for entry in secured_restricted.get(CATALOG, headers=auth_header()).json()]
    assert "material-overview" not in names
    assert "supplier-risk" in names               # unrestricted, still visible


def test_the_catalog_shows_the_product_once_the_role_is_there(secured_restricted: TestClient, auth_header: AuthHeader) -> None:
    header = auth_header(roles=["planner"])
    names = [entry["name"] for entry in secured_restricted.get(CATALOG, headers=header).json()]
    assert "material-overview" in names


def test_a_restricted_product_is_404_not_403_by_name(secured_restricted: TestClient, auth_header: AuthHeader) -> None:
    """Answering 403 here would confirm the product exists -- the error code
    itself would become a directory of what is hidden."""
    response = secured_restricted.get(f"{CATALOG}/material-overview", headers=auth_header())
    assert response.status_code == 404


# --- Configuration ----------------------------------------------------------

def test_an_issuer_without_an_audience_is_a_configuration_error(settings: Settings, auth_header: AuthHeader) -> None:
    """Without `aud` every token from the realm would be accepted, including
    one a user got for a completely different application."""
    half = settings.model_copy(update={"oidc_issuer": "https://keycloak.test/realms/airbus"})
    with TestClient(create_app(half), raise_server_exceptions=False) as client:
        response = client.get(PRODUCT, headers=auth_header())
    assert response.status_code == 500
    assert response.json()["code"] == "configuration_error"


def test_prod_refuses_to_start_without_authentication(settings: Settings) -> None:
    """A forgotten OIDC_ISSUER leaves the API open and nothing would say so.
    A server that does not come up gets noticed; an open one may not."""
    from data_api.core.errors import ConfigurationError

    unprotected_prod = settings.model_copy(update={"api_env": "prod", "oidc_issuer": None})
    with pytest.raises(ConfigurationError, match="ALLOW_ANONYMOUS"), \
            TestClient(create_app(unprotected_prod)):
        pass


def test_prod_starts_unauthenticated_when_that_is_written_down(settings: Settings) -> None:
    """A closed network is a legitimate reason to run without authentication.

    The flag does not weaken the check -- it turns "forgot to configure it" into
    "decided against it", which is the difference the guard exists to see. The
    warning keeps the state visible in the log of every start rather than only
    in whoever's memory set the variable.

    Not `caplog`: `create_app` calls `configure_logging`, which REPLACES the root
    handlers (deliberately -- see core/logging.py) and throws caplog's handler
    out with them. A handler on the module logger survives that.
    """
    messages: list[str] = []
    collector = logging.Handler()
    collector.emit = lambda record: messages.append(record.getMessage())  # type: ignore[method-assign]
    app_logger = logging.getLogger("data_api.app")
    app_logger.addHandler(collector)

    deliberate = settings.model_copy(update={
        "api_env": "prod", "oidc_issuer": None, "allow_anonymous": True,
    })
    try:
        with TestClient(create_app(deliberate)) as client:
            assert client.get(CATALOG).status_code == 200    # no token, still open
    finally:
        app_logger.removeHandler(collector)

    assert any("Authentication is OFF" in message for message in messages)


def test_the_escape_hatch_does_not_apply_outside_prod(settings: Settings) -> None:
    """dev and staging are open anyway -- the flag has nothing to do there."""
    for environment in ("dev", "staging"):
        relaxed = settings.model_copy(update={"api_env": environment, "oidc_issuer": None})
        with TestClient(create_app(relaxed)) as client:
            assert client.get(CATALOG).status_code == 200


# --- Route-level roles for hand-written endpoints ---------------------------

def test_writing_needs_more_than_reading(secured: TestClient, auth_header: AuthHeader) -> None:
    """`requires()` on the route, next to the route -- the same idea as
    `required_groups` on a data product. Reading master data and rewriting it
    are different privileges, so a read token must not be enough."""
    payload = {"material_number": "MAT-1", "target_material_group": "Rohstoffe"}

    reader = secured.post("/api/v1/mappings", headers=auth_header(roles=["viewer"]),
                          json=payload)
    assert reader.status_code == 403
    assert reader.json()["code"] == "forbidden"
    assert "material-planner" in reader.json()["detail"]

    planner = secured.post("/api/v1/mappings",
                           headers=auth_header(roles=["material-planner"]), json=payload)
    assert planner.status_code == 201


def test_the_required_role_is_documented_in_openapi(secured: TestClient, auth_header: AuthHeader) -> None:
    """A 403 nobody can find in /docs is a support ticket waiting to happen."""
    schema = secured.get("/openapi.json", headers=auth_header()).json()
    assert "403" in schema["paths"]["/api/v1/mappings"]["post"]["responses"]
