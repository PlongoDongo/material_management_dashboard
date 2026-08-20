"""Tests für den refaktorierten Datenzugriff -- ohne echte Datenbank.

Dank Dependency Injection (`_materials_from_session` bekommt eine Session
herein) genügt eine Fake-Session; weder ein Neo4j-Server noch ein Flask-Kontext
werden gebraucht.
"""
import pytest

pytest.importorskip("neo4j")  # überspringen, wenn der Treiber fehlt

from data import neo4j as n4  # noqa: E402
from data import repository as repo  # noqa: E402
from data.schema import COLUMNS  # noqa: E402


# -- _coerce_auth -----------------------------------------------------------
def test_coerce_auth_variants() -> None:
    assert n4._coerce_auth("neo4j/secret") == ("neo4j", "secret")
    assert n4._coerce_auth("neo4j:secret") == ("neo4j", "secret")
    assert n4._coerce_auth(("u", "p")) == ("u", "p")
    assert n4._coerce_auth(None) is None


# -- make_driver ------------------------------------------------------------
def test_make_driver_without_uri_is_none() -> None:
    """Ohne URI kein Treiber -> die App fällt auf Mock-Daten zurück."""
    assert n4.make_driver(None, None) is None
    assert n4.make_driver("", None) is None


def test_make_driver_builds_and_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    created = {}

    class FakeDriver:
        def __init__(self) -> None:
            self.verified = False

        def verify_connectivity(self) -> None:
            self.verified = True

        def close(self) -> None:
            pass

    def _factory(uri, auth=None):
        created["uri"], created["auth"] = uri, auth
        return FakeDriver()

    monkeypatch.setattr(n4.GraphDatabase, "driver", staticmethod(_factory))
    driver = n4.make_driver("bolt://x", "neo4j/secret")
    assert isinstance(driver, FakeDriver)
    assert driver.verified is True                 # verify_connectivity aufgerufen
    assert created["auth"] == ("neo4j", "secret")  # _coerce_auth angewandt


# -- _materials_from_session (Dependency Injection) -------------------------
class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


class _FakeSession:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.cypher = None

    def run(self, cypher, **_kw):
        self.cypher = cypher
        return [_FakeRecord(r) for r in self.rows]


def _full_row(**over):
    row = {c: "x" for c in COLUMNS}
    row["bestand"] = 5
    row.update(over)
    return row


def test_materials_from_session_selects_columns_and_casts() -> None:
    session = _FakeSession([_full_row(material_nr="MAT-1", bestand=5.0)])
    df = repo._materials_from_session(session)
    assert df.columns == COLUMNS                    # exakt die Schema-Spalten
    assert df["bestand"].dtype.__str__().startswith("Int")  # zu Int64 gecastet
    assert session.cypher == repo._CYPHER


def test_materials_from_session_is_pure() -> None:
    """Kein Flask-Kontext nötig -- die Session wird injiziert."""
    df = repo._materials_from_session(_FakeSession([_full_row()]))
    assert df.height == 1
