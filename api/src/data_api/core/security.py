"""
Authentication and authorisation -- OIDC access tokens from Keycloak.

The dashboard logs the user in against Keycloak and forwards the resulting
access token verbatim:

    Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6...

This module turns that token into a `Principal` -- and that is the whole point
of the module. Everything downstream (routers, data products) only ever sees
`Principal`, never a token, never Keycloak. Swapping the identity provider
means rewriting `current_principal` and nothing else.

WHY THE TOKEN AND NOT THE CLAIMS
================================
It is tempting to let the dashboard send what it already knows -- something
like `X-User: m.renner` and `X-Groups: planner,admin`. Do not. Those headers
are plain text: anyone who can reach the API can set them, and the whole
authorisation scheme collapses into a suggestion. A JWT carries the same
information SIGNED by Keycloak, and this module verifies that signature. The
API stays the authority; the dashboard is just a courier.

WHAT IS CHECKED
===============
Signature (against the realm's public keys), `exp`, `iss` and `aud`. Dropping
any one of them is a hole: an expired token, a token from another realm, or a
token minted for a different client would otherwise pass.

TWO LAYERS, ON PURPOSE
======================
The dashboard hides pages a user may not see; this API refuses them. That is
defence in depth, not duplication -- the dashboard's check is a courtesy to the
user, this one is the one that counts, because `curl` skips the dashboard.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header
from jwt import PyJWKClient
from starlette.concurrency import run_in_threadpool

from data_api.core.config import Settings, get_settings
from data_api.core.errors import ConfigurationError, ForbiddenError, UnauthorizedError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """Who is asking? The application's own idea of an identity.

    Deliberately NOT the token and not a Keycloak object: this is the
    translation into terms the rest of the code understands, the same move
    `db/sources.py` makes when it turns a Neo4j `ServiceUnavailable` into an
    `UpstreamUnavailableError`.

    `frozen=True` because an authorisation object that can be modified
    mid-request is a liability -- nobody should be able to grant themselves a
    group between the check and the query.
    """

    subject: str                                        # Keycloak `sub` (a stable UUID)
    username: str = ""                                  # `preferred_username`, for humans
    groups: frozenset[str] = field(default_factory=frozenset)
    auth_enabled: bool = True

    @property
    def label(self) -> str:
        """Readable identity for audit fields and logs.

        `sub` is a UUID -- correct but useless in a "changed by" column. The
        username is what a person recognises, with the UUID as fallback for
        service accounts that have none.
        """
        return self.username or self.subject

    def may_access(self, required_groups: Iterable[str]) -> bool:
        """May this caller see something that declares `required_groups`?

        When authentication is switched off (development), everything is open.
        Without that line development would be *stricter* than production: the
        anonymous caller only has the group "public" and would get a 403 on a
        product with required_groups=("internal",) -- even though auth is off.
        """
        if not self.auth_enabled:
            return True
        return not required_groups or bool(self.groups.intersection(required_groups))


ANONYMOUS = Principal(
    subject="anonymous", username="anonymous",
    groups=frozenset({"public"}), auth_enabled=False,
)


@lru_cache
def _jwk_client(jwks_uri: str) -> PyJWKClient:
    """One key client per realm, cached for the life of the process.

    `PyJWKClient` fetches the realm's public keys once and keeps them. It
    refetches when a token names a key id it has not seen -- which is what
    makes Keycloak's key rotation a non-event here.
    """
    return PyJWKClient(jwks_uri, cache_keys=True)


def _signing_key(token: str, settings: Settings) -> Any:  # noqa: ANN401
    """The public key that matches this token's `kid`.

    ANN401: PyJWT exposes `.key` untyped -- it is one of several cryptography
    key classes depending on the algorithm. Naming one would be wrong.

    A separate function so tests can replace it with a fixed key instead of
    standing up a JWKS endpoint -- everything below it (signature, exp, iss,
    aud) then still runs for real.
    """
    return _jwk_client(settings.oidc_jwks_uri).get_signing_key_from_jwt(token).key


def _groups_from(claims: dict[str, Any], settings: Settings) -> frozenset[str]:
    """Keycloak roles and groups -> `Principal.groups`.

    Keycloak scatters them across three places, and which one a deployment uses
    is a matter of realm configuration rather than of principle:

        realm_access.roles                  realm roles
        resource_access.<client>.roles      client roles
        groups                              only with a group mapper, as "/planners"

    All three are merged, so a data product can just say
    `required_groups=("planner",)` without caring how the realm is wired. Group
    paths lose their leading slash to match the flat role names.
    """
    realm = claims.get("realm_access", {}).get("roles", []) or []
    client_id = settings.oidc_client_id or settings.oidc_audience or ""
    client = claims.get("resource_access", {}).get(client_id, {}).get("roles", []) or []
    groups = [group.lstrip("/") for group in claims.get("groups", []) or []]
    return frozenset(realm) | frozenset(client) | frozenset(groups)


async def current_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Validates the bearer token and yields the caller. The one auth entry point."""
    if not settings.auth_enabled:
        return ANONYMOUS

    if not settings.oidc_audience:
        # Without an audience every token from the realm would be accepted --
        # including one the user got for a completely different application.
        raise ConfigurationError("OIDC_ISSUER is set but OIDC_AUDIENCE is missing.")

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("An `Authorization: Bearer <token>` header is required.")

    try:
        # PyJWKClient is synchronous and hits the network on a cache miss.
        # Called directly it would block the event loop for EVERY request in
        # flight, not just this one.
        key = await run_in_threadpool(_signing_key, token, settings)
        claims = jwt.decode(
            token,
            key,
            # Fixed, never taken from the token header: accepting the token's
            # own `alg` is how the classic "alg: none" forgery works.
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            leeway=settings.oidc_leeway_seconds,
            # Presence, not just validity: without this a token missing `exp`
            # would count as "never expires".
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as error:
        # The reason goes to the log, not to the caller. "Signature expired" vs
        # "wrong audience" is useful to an operator and a hint to an attacker.
        log.info("Token rejected: %s", error)
        raise UnauthorizedError("The token is invalid or has expired.") from error

    return Principal(
        subject=claims["sub"],
        username=claims.get("preferred_username", ""),
        groups=_groups_from(claims, settings),
    )


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def requires(*groups: str) -> Callable[..., Any]:
    """Route-level guard for endpoints that are not data products.

        @router.post("", dependencies=[Depends(requires("material-planner"))])

    Data products declare `required_groups` on the product itself and are
    checked in products/router.py; this is the equivalent for hand-written
    routes. Both end up calling `Principal.may_access`, so there is one rule,
    not two.
    """

    async def _guard(principal: CurrentPrincipal) -> Principal:
        if not principal.may_access(groups):
            log.info("Access denied for %s: needs one of %s, has %s",
                     principal.label, sorted(groups), sorted(principal.groups))
            raise ForbiddenError(
                f"This endpoint requires one of the roles: {', '.join(sorted(groups))}."
            )
        return principal

    # Readable back off the route, the same way `invalidates` exposes its
    # products: tests/test_architecture.py checks that every write route has a
    # guard, and architecture.py puts the role in the diagram.
    _guard.required_groups = groups
    return _guard
