"""
Keycloak login for the dashboard (OIDC authorization code flow).

WHAT HAPPENS HERE
=================
Dash is a Flask application (`app.server` is the Flask server). That is why the
login hangs off Flask and not off Dash: three routes plus a `before_request`
sentry in front of them.

    1. User opens /                -> not logged in -> redirect to /login
    2. /login                      -> redirect to Keycloak
    3. User logs in                -> Keycloak redirects back to /auth/callback
    4. /auth/callback              -> exchange code for tokens, store in session
    5. Redirect to the page originally requested

From then on the access token lives in the Flask session. `access_token()`
retrieves it again inside callbacks -- Dash callbacks run within a Flask request
context, which is why `session` works there just like anywhere else.

WHY THE TOKEN IS PASSED ON AND NOT THE ROLES
============================================
The API is handed the token unchanged and verifies it itself. The obvious
alternative would be to send it `X-User: m.renner` and
`X-Groups: planner,admin` instead -- but such headers are plain text. Anyone who
can reach the API can set them freely, and the entire permission check would
become a request rather than a control. The JWT carries the same information,
but SIGNED by Keycloak.

TWO LAYERS ON PURPOSE
=====================
This module decides which pages a user gets to see; the API decides which data
they get. That is not duplication: the check here is a courtesy towards the user
(no menu entries that lead nowhere), the one in the API is the one that
matters -- because `curl` bypasses this dashboard entirely.

CONFIGURATION (.env)
====================
    KEYCLOAK_ISSUER=https://keycloak.example.com/realms/airbus
    KEYCLOAK_CLIENT_ID=material-dashboard
    KEYCLOAK_CLIENT_SECRET=...
    FLASK_SECRET_KEY=...              # signs the session cookie
    DATA_API_AUDIENCE=data-api        # so that the token is valid for the API

Without KEYCLOAK_ISSUER the login stays OFF (development) -- exactly as in the
API, where a missing OIDC_ISSUER means the same thing.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

from flask import Flask, redirect, request, session, url_for

log = logging.getLogger(__name__)

# These paths have to be reachable without logging in, otherwise nobody can log
# in (/login) and Dash cannot load its own JavaScript (/_dash-*, /assets/*) --
# the page would stay blank.
PUBLIC_PREFIXES = ("/login", "/auth/", "/logout", "/_dash-component-suites",
                   "/_dash-layout", "/_dash-dependencies", "/assets", "/_favicon.ico")

# How many seconds before expiry we already refresh. Without that lead time the
# token expires between the check and its arrival at the API -- rare, but exactly
# the kind of bug that cannot be reproduced.
REFRESH_MARGIN_SECONDS = 30


def auth_enabled() -> bool:
    """Login is on as soon as an issuer is configured."""
    return bool(os.getenv("KEYCLOAK_ISSUER"))


def _oauth_client(server: Flask):
    """Registers the Keycloak client once on the Flask server.

    `server_metadata_url` points at the discovery document -- Authlib fetches
    endpoints and signing keys from it by itself. A typo in a URL therefore shows
    up on the first login and not only at the token exchange.
    """
    from authlib.integrations.flask_client import OAuth

    oauth = OAuth(server)
    issuer = os.environ["KEYCLOAK_ISSUER"].rstrip("/")
    oauth.register(
        name="keycloak",
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_id=os.environ["KEYCLOAK_CLIENT_ID"],
        client_secret=os.environ.get("KEYCLOAK_CLIENT_SECRET"),
        client_kwargs={
            # `openid` is mandatory. The API additionally checks the `aud` claim,
            # which is why its client id has to be requested here as well --
            # otherwise the user gets a valid token that the API rejects.
            "scope": f"openid profile email {os.getenv('DATA_API_AUDIENCE', '')}".strip(),
        },
    )
    return oauth.keycloak


def register_auth(server: Flask) -> None:
    """Attaches login, callback, logout and the sentry to the Flask server."""
    server.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)

    if not auth_enabled():
        log.warning("KEYCLOAK_ISSUER is not set -- login OFF "
                    "(acceptable for development only).")
        return

    keycloak = _oauth_client(server)

    @server.route("/login")
    def login():
        # Where to go after the login? The sentry below stores the target. Only
        # relative paths are accepted: a "?next=https://some-other-site" would be
        # an open redirect, one that lets phishing links be built which start out
        # on the genuine dashboard domain.
        target = session.get("next_url", "/")
        if urlparse(target).netloc:
            target = "/"
        session["next_url"] = target
        return keycloak.authorize_redirect(url_for("auth_callback", _external=True))

    @server.route("/auth/callback")
    def auth_callback():
        try:
            tokens = keycloak.authorize_access_token()
        except Exception as error:                      # noqa: BLE001
            # Expired state, aborted login, misconfigured client -- from the
            # user's point of view all the same thing: try again.
            log.warning("Login failed: %s", error)
            return redirect(url_for("login"))

        session["tokens"] = tokens
        session["userinfo"] = tokens.get("userinfo", {})
        log.info("Logged in: %s", username() or "?")
        return redirect(session.pop("next_url", "/"))

    @server.route("/logout")
    def logout():
        tokens = session.get("tokens") or {}
        session.clear()
        # Log out at Keycloak too, not just locally: otherwise the user is right
        # back in at the next /login without entering a password -- which on a
        # shared machine is the exact opposite of logging out.
        issuer = os.environ["KEYCLOAK_ISSUER"].rstrip("/")
        end_session = f"{issuer}/protocol/openid-connect/logout"
        home = url_for("index", _external=True) if "index" in server.view_functions else "/"
        return redirect(
            f"{end_session}?post_logout_redirect_uri={home}"
            f"&id_token_hint={tokens.get('id_token', '')}"
        )

    @server.before_request
    def require_login():
        """The sentry. Runs before EVERY request, including Dash callbacks."""
        if any(request.path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return None
        if session.get("tokens"):
            return None
        # Remember the target so that after the login the user ends up where they
        # wanted to go -- and not always on the start page.
        session["next_url"] = request.full_path if request.query_string else request.path
        return redirect(url_for("login"))


def access_token() -> str | None:
    """The current user's access token -- or None when auth is off.

    This is called inside Dash callbacks and therefore has to work even when no
    request is in flight (e.g. in tests): in that case it returns None instead of
    blowing up.
    """
    if not auth_enabled():
        return None
    try:
        tokens = session.get("tokens")
    except RuntimeError:            # outside of a request context
        return None
    if not tokens:
        return None
    if _expired(tokens):
        tokens = _refresh(tokens)
        if tokens is None:
            return None
    return tokens.get("access_token")


def user_roles() -> frozenset[str]:
    """Roles and groups of the logged-in user -- for driving the menu.

    Meant for display ONLY. The binding check is done by the API; the point here
    is not to offer menu entries that would return 403 anyway.
    """
    token = access_token()
    if not token:
        return frozenset()
    claims = _claims_without_verification(token)
    realm = claims.get("realm_access", {}).get("roles", []) or []
    client_id = os.getenv("DATA_API_AUDIENCE", "")
    client = claims.get("resource_access", {}).get(client_id, {}).get("roles", []) or []
    groups = [group.lstrip("/") for group in claims.get("groups", []) or []]
    return frozenset(realm) | frozenset(client) | frozenset(groups)


def username() -> str:
    """Display name of the logged-in user, for the header."""
    try:
        info = session.get("userinfo") or {}
    except RuntimeError:
        return ""
    return info.get("preferred_username") or info.get("name") or ""


def _expired(tokens: dict[str, Any]) -> bool:
    expires_at = tokens.get("expires_at")
    return bool(expires_at) and time.time() > expires_at - REFRESH_MARGIN_SECONDS


def _refresh(tokens: dict[str, Any]) -> dict[str, Any] | None:
    """Uses the refresh token to fetch a new access token.

    Without this a user is logged out in the middle of their work once the token
    lifetime (five minutes by default in Keycloak) has elapsed.
    """
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return None
    try:
        from authlib.integrations.requests_client import OAuth2Session

        issuer = os.environ["KEYCLOAK_ISSUER"].rstrip("/")
        client = OAuth2Session(
            client_id=os.environ["KEYCLOAK_CLIENT_ID"],
            client_secret=os.environ.get("KEYCLOAK_CLIENT_SECRET"),
        )
        fresh = client.refresh_token(
            f"{issuer}/protocol/openid-connect/token", refresh_token=refresh_token
        )
    except Exception as error:                          # noqa: BLE001
        # Refresh token expired or revoked as well: discard the session, the
        # sentry then sends the user to the login.
        log.info("Token refresh failed (%s) -- discarding the session.", error)
        session.pop("tokens", None)
        return None

    session["tokens"] = fresh
    return fresh


def _claims_without_verification(token: str) -> dict[str, Any]:
    """Reads the payload of a JWT WITHOUT verifying the signature.

    That is admissible here and a grave mistake anywhere else: the token comes
    from our own session, it was verified by Authlib during the login, and the
    roles only drive the display. One must never read this way for an access
    decision -- that one is made by the API, and it verifies the signature.
    """
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)            # add the Base64 padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:                                   # noqa: BLE001
        return {}
