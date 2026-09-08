"""
Configuration -- the single source of truth for everything coming from outside.

Why pydantic-settings instead of os.getenv() scattered through the code?
  * Every switch lives in one place and is typed (a misspelled NEO4J_UIR fails
    at startup, not inside the first request).
  * Defaults are visibly documented.
  * In tests the Settings object can be swapped via `dependency_overrides`,
    without setting environment variables.

The variable names deliberately match the dashboard's (NEO4J_URI, NEO4J_AUTH,
NEO4J_DB) -- the same .env works for both.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Neo4j -------------------------------------------------------------
    neo4j_uri: str | None = None
    neo4j_auth: str | None = None          # "user/password" or "user:password"
    neo4j_db: str = "neo4j"

    # --- Postgres ----------------------------------------------------------
    postgres_dsn: str | None = None        # postgresql+asyncpg://...

    # --- API ---------------------------------------------------------------
    api_env: Literal["dev", "staging", "prod"] = "dev"
    api_title: str = "Data Products API"
    api_log_level: str = "INFO"

    # `NoDecode` is mandatory here, not a matter of taste: pydantic-settings
    # tries to parse complex fields (list[str]) as JSON inside the *source* --
    # that is, BEFORE any validator runs. Without NoDecode the app fails to
    # start on `API_CORS_ORIGINS=http://a,http://b`, because that is not JSON.
    # With it the raw string arrives and `_split_csv` below does its job.
    api_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- OIDC / Keycloak ---------------------------------------------------
    # Setting OIDC_ISSUER is what switches authentication ON (see auth_enabled).
    # The issuer is the realm URL; everything else -- the public keys, the
    # supported algorithms -- is discovered from it at runtime.
    #   OIDC_ISSUER=https://keycloak.example.com/realms/airbus
    oidc_issuer: str | None = None
    # The `aud` claim this API insists on. In Keycloak this is the client id of
    # the API itself; the dashboard has to request it (scope / audience mapper),
    # otherwise a token minted for a DIFFERENT client would be accepted here.
    oidc_audience: str | None = None
    # Client roles live under resource_access.<this id>.roles. Usually the same
    # value as oidc_audience; separate because Keycloak allows them to differ.
    oidc_client_id: str | None = None
    # Extra seconds of tolerance for clock skew between Keycloak and this host.
    oidc_leeway_seconds: int = 10

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allows comma-separated lists in the .env (CORS=a,b,c)."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def auth_enabled(self) -> bool:
        """Authentication is on exactly when an issuer is configured.

        Deliberately derived instead of a separate AUTH_ENABLED switch: two
        settings that can contradict each other are a bug waiting to happen.
        The flip side is that a forgotten OIDC_ISSUER leaves the API OPEN, so
        `create_app` refuses to start with api_env="prod" and auth disabled.
        """
        return bool(self.oidc_issuer)

    @property
    def oidc_jwks_uri(self) -> str:
        """Where the realm publishes its public keys.

        Hard-coded rather than read from /.well-known/openid-configuration: it
        saves a network round trip at startup, and the path has been stable
        across Keycloak versions. Swap this for real discovery if you ever put
        a non-Keycloak IdP behind it.
        """
        return f"{(self.oidc_issuer or '').rstrip('/')}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    """Read once, reuse process-wide (injectable via Depends)."""
    return Settings()
