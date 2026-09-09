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
    3. .env      SQL_HOST=x                    developer convenience
    4. file      <dir>/postgres.yaml           the pod

The first three are pydantic-settings' own sources and arrive as one merged
dict; the fourth is `_fill_in_credentials` filling in what the others left out.

That order is deliberate. The credentials file is the base and normally the only
source in production, but a developer can override a single value with an
environment variable without editing (or faking) the file.

WHY THE FILE IS READ IN A VALIDATOR AND NOT AS AN IMPORT-TIME DEFAULT
=====================================================================
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

`_fill_in_credentials` below moves the same work into a `model_validator`: the
file is read when Settings is INSTANTIATED, a missing file degrades to "no
values from here" instead of an exception, and the passwords are `SecretStr` --
see the `__main__` block at the bottom for what that buys. It is ten lines of
ordinary Python and one dict merge; there is no framework machinery to learn.

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
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

# The platform mounts ONE file per data source. Its keys become Settings fields,
# prefixed with the name below -- so `host` in postgres.project fills `sql_host`:
#
#     neo4j.dev         protocol, host, port, username, password
#                       -> neo4j_protocol, neo4j_host, neo4j_port, ...
#     postgres.project  host, port, username, password, database, ssl
#                       -> sql_host, sql_port, sql_username, ...
#
# The file names are spelled out rather than guessed from a suffix: the platform
# picks them, and `.dev` / `.project` are not something this code can derive. If
# another environment mounts them under different names, this dict is the one
# place to change.
#
# A key the Settings class below does not declare is ignored (extra="ignore"),
# so an extra entry in the platform's file cannot break the start.
_CREDENTIAL_FILES = {
    "neo4j.dev": "neo4j",
    "postgres.project": "sql",
}


def load_credentials() -> dict[str, Any]:
    """Reads the mounted credential files into Settings field values.

    Called during validation, NOT at import -- see the module docstring for why
    that distinction is the whole point of this file.
    """
    values: dict[str, Any] = {}
    directory = credentials_dir()
    for file_name, prefix in _CREDENTIAL_FILES.items():
        path = directory / file_name
        if not path.is_file():
            # Not an error: development and CI have no mounted credentials, and
            # the app is expected to start and report the source as inactive.
            log.debug("No credentials file %s.", path)
            continue
        for key, value in _read_file(path).items():
            values[f"{prefix}_{key}"] = value
    return values


def _read_file(path: Path) -> dict[str, Any]:
    """Parses one credentials file into a plain dict.

    The file extension is irrelevant -- `neo4j.dev` is read exactly like
    `neo4j.yaml`. `yaml.safe_load` covers both YAML and JSON (JSON is a subset
    of YAML), which between them are what such a file realistically contains.

    A file that EXISTS but cannot be parsed is an error, unlike a missing one:
    swallowing it would start the pod with no database and no explanation. The
    message names the path but NEVER the content -- that would put the password
    in the log, which is the thing this module is built to prevent.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            content = yaml.safe_load(handle) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Credentials file {path} is unreadable: {error}") from error
    if not isinstance(content, dict):
        raise ConfigurationError(
            f"Credentials file {path} does not parse into key/value pairs "
            f"(got {type(content).__name__}). Expected YAML or JSON, e.g. "
            f"'host: db.intern'. A 'KEY=value' file would land here."
        )
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
    # 127.0.0.1 by default: reachable only from this machine. A container
    # sets SERVER_HOST=0.0.0.0 to listen on every interface -- opening that
    # up should be a deliberate act, not the default.
    server_host: str = "127.0.0.1"
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
    neo4j_username: str | None = None
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

    @model_validator(mode="before")
    @classmethod
    def _fill_in_credentials(cls, values: Any) -> Any:  # noqa: ANN401
        """Fills every field the environment did not already provide.

        `values` is what init, the environment and .env produced together.
        Spreading it LAST means those three win and the file only fills gaps --
        which is what lets `NEO4J_HOST=localhost` override a mounted file
        without editing it.
        """
        if not isinstance(values, dict):
            return values
        return {**load_credentials(), **values}

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
        if not self.neo4j_username or self.neo4j_password is None:
            return None
        return (self.neo4j_username, self.neo4j_password.get_secret_value())

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
