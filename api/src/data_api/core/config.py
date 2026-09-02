"""
Konfiguration -- die EINE Wahrheit fuer alles, was von aussen kommt.

Warum pydantic-settings statt os.getenv() im ganzen Code verteilt?
  * Alle Schalter stehen an einer Stelle und sind typisiert (ein falsch
    geschriebenes NEO4J_UIR faellt beim Start auf, nicht im ersten Request).
  * Defaults sind sichtbar dokumentiert.
  * In Tests laesst sich das Settings-Objekt ueber `dependency_overrides`
    austauschen, ohne Umgebungsvariablen zu setzen.

Die Variablennamen sind absichtlich identisch zu denen des Dashboards
(NEO4J_URI, NEO4J_AUTH, NEO4J_DB) -- dieselbe .env funktioniert fuer beide.
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
    neo4j_auth: str | None = None          # "user/passwort" oder "user:passwort"
    neo4j_db: str = "neo4j"

    # --- Postgres ----------------------------------------------------------
    postgres_dsn: str | None = None        # postgresql+asyncpg://...

    # --- API ---------------------------------------------------------------
    api_env: Literal["dev", "staging", "prod"] = "dev"
    api_title: str = "Data Products API"
    api_log_level: str = "INFO"

    # `NoDecode` ist hier PFLICHT, nicht Geschmack: pydantic-settings versucht
    # komplexe Felder (list[str]) schon in der Quelle als JSON zu lesen -- also
    # BEVOR ein Validator laeuft. Ohne NoDecode scheitert der Start an
    # `API_CORS_ORIGINS=http://a,http://b`, weil das kein JSON ist. Mit NoDecode
    # kommt der Rohtext an und `_split_csv` unten greift wie vorgesehen.
    api_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("api_cors_origins", "api_keys", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Erlaubt kommagetrennte Listen in der .env (CORS=a,b,c)."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)


@lru_cache
def get_settings() -> Settings:
    """Einmal lesen, prozessweit wiederverwenden (per Depends injizierbar)."""
    return Settings()
