"""
Configuration: the mounted credential files as a fallback for unset fields.

The failure modes this guards against are all quiet ones. A credentials loader
that reads at the wrong time passes on the pod and breaks in CI; one that leaks
a password does so into a log nobody reads until it matters; one that ignores a
malformed file starts an application with no database and no explanation.
"""
from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path

import pytest
import yaml

from core.config import _CREDENTIAL_FILES, Settings
from core.errors import ConfigurationError

NEO4J_FILE = {
    "protocol": "bolt",
    "host": "neo4j.intern",
    "port": 7687,
    "username": "neo4j",
    "password": "s3hr-geheim",
}
POSTGRES_FILE = {
    "host": "pg.intern",
    "port": 5432,
    "username": "analytics",
    "password": "auch-geheim",
    "database": "warehouse",
    "ssl": "require",
}


@pytest.fixture
def mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory that looks like the platform's mount."""
    (tmp_path / "neo4j.dev").write_text(yaml.safe_dump(NEO4J_FILE), encoding="utf-8")
    (tmp_path / "postgres.project").write_text(yaml.safe_dump(POSTGRES_FILE), encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))
    return tmp_path


def _settings(**overrides: object) -> Settings:
    """Settings without a developer's .env interfering."""
    return Settings(_env_file=None, **overrides)


# --- The file is read, and read at the right time ---------------------------

def test_the_file_fills_the_fields(mounted: Path) -> None:
    settings = _settings()

    assert settings.neo4j_host == "neo4j.intern"
    assert settings.neo4j_username == "neo4j"
    assert settings.sql_database == "warehouse"
    assert settings.sql_ssl == "require"


def test_importing_the_module_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE point of the whole construction.

    With the loader as an import-time default, `import` itself fails without the
    file -- locally, in CI and during `pytest --collect-only` -- and the
    traceback points at an import line rather than at the code that needs
    credentials.
    """

    monkeypatch.setenv("CREDENTIALS_DIR", "/definitely/not/here")
    module = importlib.import_module("core.config")
    importlib.reload(module)          # no exception, nothing read


def test_a_missing_directory_leaves_the_sources_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-safe: development and CI have no mount, and the app must still start.
    The data sources then report themselves as inactive, as they always have."""
    monkeypatch.setenv("CREDENTIALS_DIR", "/definitely/not/here")
    settings = _settings()

    assert settings.neo4j_host is None
    assert settings.neo4j_uri is None
    assert settings.sql_url is None


def test_a_rotated_file_is_picked_up_by_a_fresh_settings(mounted: Path) -> None:
    """Read on instantiation, not at import.

    In today's architecture a rotation still needs a restart (get_settings is
    lru_cached, and the driver is built once in the lifespan) -- but the value
    is no longer baked into the class, which is what makes reconnect-on-auth-
    failure possible later.
    """
    assert _settings().neo4j_password.get_secret_value() == "s3hr-geheim"

    rotated = dict(NEO4J_FILE, password="rotiert")
    (mounted / "neo4j.dev").write_text(yaml.safe_dump(rotated), encoding="utf-8")

    assert _settings().neo4j_password.get_secret_value() == "rotiert"


def test_the_file_extension_is_irrelevant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`neo4j.dev` is read exactly like `neo4j.yaml` would be.

    The platform picks the names; `.dev` and `.project` are not something this
    code could derive, so they are spelled out in _CREDENTIAL_FILES and the
    parser never looks at the suffix.
    """

    assert set(_CREDENTIAL_FILES) == {"neo4j.dev", "postgres.project"}
    (tmp_path / "neo4j.dev").write_text(yaml.safe_dump(NEO4J_FILE), encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    assert _settings().neo4j_host == "neo4j.intern"


def test_a_json_file_is_read_just_as_well(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """We do not actually know which of the two the platform writes.

    JSON is a subset of YAML, so `yaml.safe_load` covers both -- which means the
    format question does not have to be answered before this works.
    """

    (tmp_path / "neo4j.dev").write_text(json.dumps(NEO4J_FILE), encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    assert _settings().neo4j_host == "neo4j.intern"
    assert _settings().neo4j_port == 7687


def test_a_key_value_file_says_what_is_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`host=x` (dotenv style) parses as a plain string, not a mapping.

    The most likely way the format assumption turns out wrong, so the error
    names the expected shape instead of just failing.
    """
    (tmp_path / "neo4j.dev").write_text("host=neo4j.intern\nport=7687\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    with pytest.raises(ConfigurationError, match="key/value pairs"):
        _settings()


# --- Precedence -------------------------------------------------------------

def test_an_environment_variable_beats_the_file(
    mounted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What makes local work possible on a machine that HAS the mount: override
    one value without editing or faking the file."""
    monkeypatch.setenv("NEO4J_HOST", "localhost")
    settings = _settings()

    assert settings.neo4j_host == "localhost"
    assert settings.neo4j_username == "neo4j"          # still from the file


def test_an_explicit_argument_beats_everything(
    mounted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook the whole test suite hangs on: `create_app(settings)`."""
    monkeypatch.setenv("SQL_HOST", "from-env")

    assert _settings(sql_host="from-init").sql_host == "from-init"


# --- Secrets stay secret ----------------------------------------------------

def test_passwords_are_redacted_in_repr_dump_and_logs(
    mounted: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A plain dict does not know it holds a password; SecretStr does.

    This covers the three ways one escapes by accident: printing the object,
    dumping it, and logging it. In a container all three end up in the log
    aggregator, indexed and retained.
    """
    settings = _settings()
    with caplog.at_level(logging.WARNING):
        logging.getLogger(__name__).warning("config: %s", settings)

    for text in (repr(settings), str(settings.model_dump()), caplog.text):
        assert "s3hr-geheim" not in text
        assert "auch-geheim" not in text
        assert "**********" in text


def test_the_password_is_still_reachable_on_purpose(mounted: Path) -> None:
    """Redacted, not hidden -- the driver has to get it somehow."""
    settings = _settings()

    assert settings.neo4j_auth == ("neo4j", "s3hr-geheim")
    assert "auch-geheim" in settings.sql_url


# --- A broken file is not the same as a missing one -------------------------

def test_unparseable_yaml_is_an_error_not_an_empty_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody put something there and it is broken. Swallowing that would
    start the pod with no database and no clue why."""
    (tmp_path / "neo4j.dev").write_text("host: [unclosed\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    with pytest.raises(ConfigurationError, match="unreadable"):
        _settings()


def test_a_yaml_that_is_not_a_mapping_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "postgres.project").write_text("- just\n- a list\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    with pytest.raises(ConfigurationError, match="key/value pairs"):
        _settings()


def test_a_wrongly_typed_value_names_the_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gain over `int(creds["port"])`: the error says WHICH field, at
    startup, instead of a ValueError from inside an import."""
    (tmp_path / "neo4j.dev").write_text(
        yaml.safe_dump(dict(NEO4J_FILE, port="not-a-number")), encoding="utf-8"
    )
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="neo4j_port"):
        _settings()


# --- Derived values ---------------------------------------------------------

def test_the_uris_are_assembled_from_the_parts(mounted: Path) -> None:
    settings = _settings()

    assert settings.neo4j_uri == "bolt://neo4j.intern:7687"
    assert settings.sql_url == (
        "postgresql+asyncpg://analytics:auch-geheim@pg.intern:5432/warehouse"
    )


def test_the_sql_url_always_names_the_async_driver(mounted: Path) -> None:
    """`postgresql://` alone selects psycopg2, which blocks the event loop for
    every request in flight. Not a knob anyone should be able to turn."""
    assert _settings().sql_url.startswith("postgresql+asyncpg://")


def test_incomplete_parts_yield_no_uri_rather_than_a_broken_one() -> None:
    """A half-built DSN would fail at connect time with a confusing message;
    None routes into the existing "source inactive" path instead."""
    assert _settings(neo4j_host=None).neo4j_uri is None
    assert _settings(sql_host="pg", sql_database=None).sql_url is None


def test_the_loglevel_is_validated_against_its_allowed_values() -> None:
    """A Literal instead of a plain str: `SERVER_LOGLEVEL=verbose` is a startup
    error naming the choices, not a silently wrong level."""
    assert _settings(server_loglevel="debug").server_loglevel == "debug"
    with pytest.raises(ValueError, match="server_loglevel"):
        _settings(server_loglevel="verbose")
