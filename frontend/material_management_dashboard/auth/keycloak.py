"""
Keycloak-Anmeldung fuer das Dashboard (OIDC Authorization Code Flow).

WAS HIER PASSIERT
=================
Dash ist eine Flask-Anwendung (`app.server` ist der Flask-Server). Deshalb
haengt die Anmeldung an Flask, nicht an Dash: drei Routen plus ein
`before_request`-Wachposten davor.

    1. Nutzer oeffnet /            -> nicht angemeldet -> Weiterleitung /login
    2. /login                      -> Weiterleitung zu Keycloak
    3. Nutzer meldet sich an       -> Keycloak leitet auf /auth/callback zurueck
    4. /auth/callback              -> Code gegen Token tauschen, in die Session
    5. Weiterleitung auf die urspruenglich gewuenschte Seite

Ab dann liegt das Access-Token in der Flask-Session. `access_token()` holt es
in Callbacks wieder heraus -- Dash-Callbacks laufen innerhalb eines
Flask-Request-Kontexts, deshalb funktioniert `session` dort ganz normal.

WARUM DAS TOKEN UND NICHT DIE ROLLEN WEITERGEREICHT WERDEN
==========================================================
Die API bekommt das Token unveraendert weitergereicht und prueft es selbst.
Naheliegend waere, ihr stattdessen `X-User: m.renner` und
`X-Groups: planner,admin` zu schicken -- aber solche Header sind einfacher
Text. Wer die API erreicht, kann sie frei setzen, und die gesamte
Rechtepruefung waere damit eine Bitte statt einer Kontrolle. Das JWT enthaelt
dieselbe Information, aber von Keycloak SIGNIERT.

ZWEI EBENEN MIT ABSICHT
=======================
Dieses Modul entscheidet, welche Seiten ein Nutzer zu sehen bekommt; die API
entscheidet, welche Daten er bekommt. Das ist keine Doppelung: Die Pruefung
hier ist eine Hoeflichkeit gegenueber dem Nutzer (keine Menuepunkte, die ins
Leere fuehren), die in der API ist die, auf die es ankommt -- denn `curl`
kommt an diesem Dashboard vorbei.

KONFIGURATION (.env)
====================
    KEYCLOAK_ISSUER=https://keycloak.example.com/realms/airbus
    KEYCLOAK_CLIENT_ID=material-dashboard
    KEYCLOAK_CLIENT_SECRET=...
    FLASK_SECRET_KEY=...              # signiert das Session-Cookie
    DATA_API_AUDIENCE=data-api        # damit das Token fuer die API gilt

Ohne KEYCLOAK_ISSUER bleibt die Anmeldung AUS (Entwicklung) -- genauso wie in
der API, wo ein fehlender OIDC_ISSUER dasselbe bedeutet.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

from flask import Flask, redirect, request, session, url_for

log = logging.getLogger(__name__)

# Diese Pfade muessen ohne Anmeldung erreichbar sein, sonst kann sich niemand
# anmelden (/login) und Dash kann sein eigenes JavaScript nicht laden
# (/_dash-*, /assets/*) -- die Seite bliebe weiss.
PUBLIC_PREFIXES = ("/login", "/auth/", "/logout", "/_dash-component-suites",
                   "/_dash-layout", "/_dash-dependencies", "/assets", "/_favicon.ico")

# Wieviele Sekunden vor Ablauf schon erneuert wird. Ohne Vorlauf laeuft das
# Token zwischen Pruefung und Ankunft bei der API ab -- selten, aber genau die
# Sorte Fehler, die sich nicht nachstellen laesst.
REFRESH_MARGIN_SECONDS = 30


def auth_enabled() -> bool:
    """Anmeldung ist an, sobald ein Issuer konfiguriert ist."""
    return bool(os.getenv("KEYCLOAK_ISSUER"))


def _oauth_client(server: Flask):
    """Registriert den Keycloak-Client einmalig am Flask-Server.

    `server_metadata_url` zeigt auf das Discovery-Dokument -- Authlib holt sich
    Endpunkte und Signaturschluessel daraus selbst. Ein Tippfehler in einer URL
    faellt damit beim ersten Login auf und nicht erst beim Token-Tausch.
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
            # `openid` ist Pflicht. Die API prueft zusaetzlich die `aud`-Claim,
            # deshalb muss ihre Client-ID hier mit angefragt werden -- sonst
            # bekommt der Nutzer ein gueltiges Token, das die API ablehnt.
            "scope": f"openid profile email {os.getenv('DATA_API_AUDIENCE', '')}".strip(),
        },
    )
    return oauth.keycloak


def register_auth(server: Flask) -> None:
    """Haengt Login, Callback, Logout und den Wachposten an den Flask-Server."""
    server.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)

    if not auth_enabled():
        log.warning("KEYCLOAK_ISSUER ist nicht gesetzt -- Anmeldung AUS "
                    "(nur fuer Entwicklung akzeptabel).")
        return

    keycloak = _oauth_client(server)

    @server.route("/login")
    def login():
        # Wohin nach dem Login? Der Wachposten unten legt das Ziel ab. Nur
        # relative Pfade werden akzeptiert: ein "?next=https://fremde-seite"
        # waere eine offene Weiterleitung, mit der sich Phishing-Links bauen
        # lassen, die auf der echten Dashboard-Domain beginnen.
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
            # Abgelaufener State, abgebrochener Login, falsch konfigurierter
            # Client -- fuer den Nutzer alles dasselbe: nochmal versuchen.
            log.warning("Login fehlgeschlagen: %s", error)
            return redirect(url_for("login"))

        session["tokens"] = tokens
        session["userinfo"] = tokens.get("userinfo", {})
        log.info("Angemeldet: %s", username() or "?")
        return redirect(session.pop("next_url", "/"))

    @server.route("/logout")
    def logout():
        tokens = session.get("tokens") or {}
        session.clear()
        # Auch bei Keycloak abmelden, nicht nur lokal: sonst ist der Nutzer
        # beim naechsten /login sofort wieder drin, ohne Passworteingabe --
        # was auf einem geteilten Rechner genau das Gegenteil von Abmelden ist.
        issuer = os.environ["KEYCLOAK_ISSUER"].rstrip("/")
        end_session = f"{issuer}/protocol/openid-connect/logout"
        home = url_for("index", _external=True) if "index" in server.view_functions else "/"
        return redirect(
            f"{end_session}?post_logout_redirect_uri={home}"
            f"&id_token_hint={tokens.get('id_token', '')}"
        )

    @server.before_request
    def require_login():
        """Der Wachposten. Laeuft vor JEDER Anfrage, auch vor Dash-Callbacks."""
        if any(request.path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return None
        if session.get("tokens"):
            return None
        # Ziel merken, damit der Nutzer nach dem Login dort landet, wo er
        # hinwollte -- und nicht immer auf der Startseite.
        session["next_url"] = request.full_path if request.query_string else request.path
        return redirect(url_for("login"))


def access_token() -> str | None:
    """Das Access-Token des aktuellen Nutzers -- oder None, wenn Auth aus ist.

    Wird in Dash-Callbacks aufgerufen und muss deshalb auch dann funktionieren,
    wenn gerade kein Request laeuft (z.B. in Tests): dann gibt es None zurueck,
    statt zu fliegen.
    """
    if not auth_enabled():
        return None
    try:
        tokens = session.get("tokens")
    except RuntimeError:            # ausserhalb eines Request-Kontexts
        return None
    if not tokens:
        return None
    if _expired(tokens):
        tokens = _refresh(tokens)
        if tokens is None:
            return None
    return tokens.get("access_token")


def user_roles() -> frozenset[str]:
    """Rollen und Gruppen des angemeldeten Nutzers -- fuer die Menuesteuerung.

    NUR fuer die Anzeige gedacht. Die verbindliche Pruefung macht die API; hier
    geht es darum, keine Menuepunkte anzubieten, die ohnehin 403 liefern.
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
    """Anzeigename des angemeldeten Nutzers, fuer den Header."""
    try:
        info = session.get("userinfo") or {}
    except RuntimeError:
        return ""
    return info.get("preferred_username") or info.get("name") or ""


def _expired(tokens: dict[str, Any]) -> bool:
    expires_at = tokens.get("expires_at")
    return bool(expires_at) and time.time() > expires_at - REFRESH_MARGIN_SECONDS


def _refresh(tokens: dict[str, Any]) -> dict[str, Any] | None:
    """Holt mit dem Refresh-Token ein neues Access-Token.

    Ohne das wird ein Nutzer nach der Token-Laufzeit (bei Keycloak per Default
    fuenf Minuten) mitten in der Arbeit abgemeldet.
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
        # Refresh-Token ebenfalls abgelaufen oder zurueckgezogen: Session
        # verwerfen, der Wachposten schickt den Nutzer zum Login.
        log.info("Token-Refresh fehlgeschlagen (%s) -- Session wird verworfen.", error)
        session.pop("tokens", None)
        return None

    session["tokens"] = fresh
    return fresh


def _claims_without_verification(token: str) -> dict[str, Any]:
    """Liest den Payload eines JWT, OHNE die Signatur zu pruefen.

    Das ist hier zulaessig und anderswo ein schwerer Fehler: Das Token kommt
    aus der eigenen Session, wurde beim Login von Authlib geprueft, und die
    Rollen steuern nur die Anzeige. Fuer eine Zugriffsentscheidung duerfte man
    so nie lesen -- die trifft die API, und die prueft die Signatur.
    """
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)            # Base64-Padding ergaenzen
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:                                   # noqa: BLE001
        return {}
