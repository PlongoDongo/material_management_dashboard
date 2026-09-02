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
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("api_cors_origins", "api_keys", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allows comma-separated lists in the .env (CORS=a,b,c)."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)


@lru_cache
def get_settings() -> Settings:
    """Read once, reuse process-wide (injectable via Depends)."""
    return Settings()
