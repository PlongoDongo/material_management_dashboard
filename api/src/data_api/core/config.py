"""
Configuration -- the single source of truth for everything coming from outside.

Why pydantic-settings instead of os.getenv() scattered through the code?
  * Every switch lives in one place and is typed (a misspelled NEO4J_HOST fails
    at startup, not inside the first request).
  * Defaults are visibly documented.
  * In tests the Settings object can be swapped via `dependency_overrides`,
    without setting environment variables.

WHERE VALUES COME FROM
======================
Four sources, and the FIRST one that has a value wins:

    1. init      Settings(sql_host="x")        tests, create_app(settings)
    2. env       SQL_HOST=x                    the deployment, and local overrides
    3. .env       SQL_HOST=x                   developer convenience
    4. credentials file  /<dir>/postgres.yaml  the pod

That order is deliberate. The credentials file is the base and normally the only
source in production, but a developer can override a single value with an
environment variable without editing (or faking) the file.

WHY THE FILE IS A SOURCE AND NOT AN IMPORT-TIME DEFAULT
=======================================================
The tempting shape is

    class Config(BaseModel):
        creds: dict = load_credentials("neo4j")     # <- runs at IMPORT
        host: str = creds["host"]

and it has three problems that all show up somewhere other than the pod:

  * The file is read when the module is IMPORTED, so `import` itself fails
    without it -- locally, in CI, and during `pytest --collect-only`. And the
    traceback points at an import line, not at the code that needs credentials.
  * The values are frozen at import. Even a fresh `Config()` returns the old
    ones, so a rotated secret never arrives.
  * `creds` is a plain dict, so the password appears in `repr()`, in
    `model_dump()` and in any log line that prints the object.

As a source, the file is read when Settings is INSTANTIATED, a missing file
degrades to "no values from here" instead of an exception, and the passwords are
`SecretStr` -- see the `__main__` block at the bottom for what that buys.

FAIL-SAFE, NOT FAIL-SILENT
==========================
A missing file is tolerated (development, CI): the fields simply stay empty, and
`create_driver` / `create_engine` then log "inactive" exactly as they do today
without NEO4J_HOST. A file that EXISTS but cannot be parsed is a different
thing -- somebody put something there and it is broken -- and raises.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from data_api.core.errors import ConfigurationError

log = logging.getLogger(__name__)

# Where the platform mounts the credentials.
DEFAULT_CREDENTIALS_DIR = "/etc/credentials"


def credentials_dir() -> Path:
    """The mount point, read WHEN ASKED rather than at import.

    A module-level `Path(os.getenv(...))` would freeze the value at import time
    -- the same mistake this whole module is written to avoid, one level up.
    As a function it stays overridable by a test (monkeypatch.setenv) and by a
    deployment that mounts somewhere else.
    """
    return Path(os.getenv("CREDENTIALS_DIR", DEFAULT_CREDENTIALS_DIR))

# credential id -> the file's keys -> our field names.
#
# This table IS the documentation of the file format, and it is the only place
# that knows it. A renamed key in the platform's YAML is a one-line change here;
# nothing else in the code sees those names.
_CREDENTIAL_FIELDS: dict[str, dict[str, str]] = {
    "neo4j": {
        "protocol": "neo4j_protocol",
        "host": "neo4j_host",
        "port": "neo4j_port",
        "username": "neo4j_user",
        "password": "neo4j_password",
    },
    "postgres": {
        "host": "sql_host",
        "port": "sql_port",
        "username": "sql_username",
        "database": "sql_database",
        "password": "sql_password",
        "ssl": "sql_ssl",
    },
}


class CredentialsFileSource(PydanticBaseSettingsSource):
    """Reads <CREDENTIALS_DIR>/<id>.yaml and maps it onto the Settings fields.

    One file per credential id, matching how the platform mounts them. Both
    `.yaml` and `.yml` are accepted so a different convention does not need a
    code change.
    """

    def get_field_value(self, field: Any, field_name: str) -> Any:  # noqa: ANN401
        # Part of the abstract interface but only used by sources that resolve
        # field by field. This one produces the whole mapping in __call__.
        raise NotImplementedError

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for credential_id, key_map in _CREDENTIAL_FIELDS.items():
            content = self._read(credential_id)
            for file_key, field_name in key_map.items():
                if file_key in content:
                    values[field_name] = content[file_key]
        return values

    def _read(self, credential_id: str) -> dict[str, Any]:
        directory = credentials_dir()
        path = next(
            (p for suffix in (".yaml", ".yml")
             if (p := directory / f"{credential_id}{suffix}").is_file()),
            None,
        )
        if path is None:
            # Not an error: development and CI have no mounted credentials, and
            # the app is expected to start and report the data source as
            # inactive. Debug level because on a developer machine this is the
            # normal case and a warning per start would just be noise.
            log.debug("No credentials file for '%s' in %s.", credential_id, directory)
            return {}
        try:
            with path.open(encoding="utf-8") as handle:
                content = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError) as error:
            # A file that exists but cannot be read is a real misconfiguration.
            # Swallowing it would start the pod with no database and no clue.
            raise ConfigurationError(f"Credentials file {path} is unreadable: {error}") from error
        if not isinstance(content, dict):
            raise ConfigurationError(f"Credentials file {path} must contain a mapping.")
        return content


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Defaults are validated too. Without this a default would be trusted
        # as-is, and a `Literal` field set from a plain string default would
        # never be checked against its allowed values.
        validate_default=True,
    )

    # --- Server ------------------------------------------------------------
    api_env: Literal["dev", "staging", "prod"] = "dev"
    api_title: str = "Data Products API"
    server_port: int = Field(8000, ge=1, le=65535)
    server_loglevel: Literal["critical", "error", "warning", "info", "debug"] = "info"

    # --- CORS --------------------------------------------------------------
    # `NoDecode` is mandatory here, not a matter of taste: pydantic-settings
    # tries to parse complex fields (list[str]) as JSON inside the *source* --
    # that is, BEFORE any validator runs. Without NoDecode the app fails to
    # start on `CORS_ORIGINS=http://a,http://b`, because that is not JSON.
    # With it the raw string arrives and `_split_csv` below does its job.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_allow_credentials: bool = True
    cors_allow_methods: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["GET", "POST", "PATCH", "PUT", "DELETE"]
    )
    cors_allow_headers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"]
    )

    # --- Neo4j -------------------------------------------------------------
    # Filled from <CREDENTIALS_DIR>/neo4j.yaml, overridable per field via the
    # environment. All optional: without them the graph is simply inactive.
    neo4j_protocol: str = "bolt"
    neo4j_host: str | None = None
    neo4j_port: int = 7687
    neo4j_user: str | None = None
    neo4j_password: SecretStr | None = None
    neo4j_db: str = "neo4j"
    neo4j_max_connection_pool_size: int = Field(50, ge=1, le=1000)
    neo4j_connection_acquisition_timeout: float = Field(2.0, gt=0)

    # --- Postgres ----------------------------------------------------------
    sql_host: str | None = None
    sql_port: int = 5432
    sql_username: str | None = None
    sql_password: SecretStr | None = None
    sql_database: str | None = None
    # Passed to asyncpg verbatim ("require", "disable", "prefer", ...). NOT
    # spliced into the URL: SQLAlchemy's asyncpg dialect does not translate an
    # `?ssl=` query parameter, so it goes through connect_args in db/sql.py.
    sql_ssl: str | None = None

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Adds the credentials file as the LAST source -- see the module docstring.

        Last means lowest precedence, which is what makes
        `SQL_HOST=localhost python -m data_api.main` work on a machine that also
        has the file mounted.
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            CredentialsFileSource(settings_cls),
            file_secret_settings,
        )

    @field_validator("cors_origins", "cors_allow_methods", "cors_allow_headers",
                     mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allows comma-separated lists in the .env (CORS_ORIGINS=a,b,c)."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # --- Derived values ----------------------------------------------------
    # Properties, not fields: they must never be settable from the outside, and
    # they must reflect the parts as they are NOW rather than as they were when
    # the object was built.

    @property
    def neo4j_uri(self) -> str | None:
        """`bolt://host:port`, or None when the graph is not configured."""
        if not self.neo4j_host:
            return None
        return f"{self.neo4j_protocol}://{self.neo4j_host}:{self.neo4j_port}"

    @property
    def neo4j_auth(self) -> tuple[str, str] | None:
        """(user, password) for the driver -- or None for an unauthenticated one.

        This is a DELIBERATE unwrapping of the SecretStr. Everywhere else the
        password stays wrapped so it cannot reach a log by accident; here it is
        being handed to the thing that needs it.
        """
        if not self.neo4j_user or self.neo4j_password is None:
            return None
        return (self.neo4j_user, self.neo4j_password.get_secret_value())

    @property
    def sql_url(self) -> str | None:
        """The async DSN, or None when Postgres is not configured.

        The driver name is fixed rather than configurable: `postgresql://` alone
        selects psycopg2, which is synchronous and would block the event loop
        for every request, not just this one. That is not a knob anyone should
        be able to turn.
        """
        if not (self.sql_host and self.sql_database):
            return None
        password = self.sql_password.get_secret_value() if self.sql_password else ""
        credentials = f"{self.sql_username}:{password}@" if self.sql_username else ""
        return (f"postgresql+asyncpg://{credentials}"
                f"{self.sql_host}:{self.sql_port}/{self.sql_database}")

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


if __name__ == "__main__":
    # `python -m data_api.core.config` prints what the application would see at
    # this point -- the quickest way to answer "did the ConfigMap and the
    # credentials actually arrive?" on a pod, without starting the server.
    #
    # `print`, not `log.warning`: this is CLI output, not a warning, and an
    # alert rule on WARNING should not fire because someone inspected the
    # config. And the OBJECT, not `sql_url` -- a URL is a plain string and a
    # plain string does not know it contains a password, while SecretStr
    # redacts itself. Host, port and user stay visible, which is what you came
    # for; the passwords show as '**********'.
    print(f"credentials dir: {credentials_dir()}")
    print(get_settings())
