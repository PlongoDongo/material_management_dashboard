"""Tests für den Neo4jManager -- ohne echte Datenbank.

Der Treiber wird durch einen Fake ersetzt (monkeypatch auf
GraphDatabase.driver), sodass Lebenszyklus und Erreichbarkeit
(__enter__ -> get_manager -> __exit__) geprüft werden, ohne dass ein
Neo4j-Server läuft.
"""
import pytest

pytest.importorskip("neo4j")  # überspringen, wenn der Treiber fehlt

import data.neo4j_manager as nm  # noqa: E402


class _FakeDriver:
    def __init__(self):
        self.closed = False
        self.queries = []

    def verify_connectivity(self):
        pass

    def close(self):
        self.closed = True

    def execute_query(self, cypher, params, database_=None):
        self.queries.append((cypher, params, database_))
        Rec = type("Rec", (), {"data": lambda self: {"material_nr": "MAT-1", "bestand": 5}})
        return ([Rec()], None, ["material_nr", "bestand"])


@pytest.fixture
def fake_drivers(monkeypatch):
    created = []

    def _factory(uri, auth=None):
        d = _FakeDriver()
        created.append(d)
        return d

    monkeypatch.setattr(nm.GraphDatabase, "driver", staticmethod(_factory))
    # sauberer Ausgangszustand, egal was vorher lief
    monkeypatch.setattr(nm, "_ACTIVE", None)
    return created


def test_get_manager_raises_when_inactive(fake_drivers):
    with pytest.raises(RuntimeError):
        nm.get_manager()


def test_with_block_publishes_same_instance(fake_drivers):
    assert nm._ACTIVE is None
    with nm.Neo4jManager("bolt://x", ("u", "p")) as db:
        # get_manager() liefert GENAU die Instanz aus dem with-Block
        assert nm.get_manager() is db
        assert db.driver is fake_drivers[0]
    # nach dem Block: geschlossen und abgemeldet
    assert nm._ACTIVE is None
    assert fake_drivers[0].closed is True


def test_no_uri_stays_idle(fake_drivers):
    """Ohne URI kein Treiber, kein aktiver Manager -> Mock-Modus."""
    with nm.Neo4jManager(uri=None, auth=None) as db:
        assert db.driver is None
        assert nm._ACTIVE is None
        assert created_count(fake_drivers) == 0


def test_fetch_dataframe_uses_configured_db(fake_drivers):
    with nm.Neo4jManager("bolt://x", ("u", "p"), db_name="materials") as db:
        df = db.fetch_dataframe("MATCH (n) RETURN n", {"a": 1})
        assert df.height == 1
        cypher, params, database_ = fake_drivers[0].queries[0]
        assert params == {"a": 1}
        assert database_ == "materials"


def test_coerce_auth():
    assert nm._coerce_auth("neo4j/secret") == ("neo4j", "secret")
    assert nm._coerce_auth("neo4j:secret") == ("neo4j", "secret")
    assert nm._coerce_auth(("u", "p")) == ("u", "p")
    assert nm._coerce_auth(None) is None


def created_count(created):
    return len(created)
