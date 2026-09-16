"""
Tests des API-Anschlusses -- ohne laufenden API-Server.

`httpx.MockTransport` faengt die Anfragen ab und antwortet mit erfundenen, aber
FORMATGLEICHEN Antworten. Damit laeuft die komplette Kette:

    get_materials() -> DataProductClient.fetch() -> HTTP-Schicht (gemockt)
                    -> Umschlag auspacken -> _rows_to_frame() -> DataFrame

Das ist derselbe Gedanke wie im API-Projekt (dort `dependency_overrides`):
Nur die aeusserste Schicht wird ersetzt, alles darueber laeuft echt.
"""
from __future__ import annotations

import httpx
import polars as pl
import pytest

from data import repository as repo
from data.api_client import DataProductClient, DataProductError, NotAuthorisedError
from data.schema import COLUMNS

# Zeilen genau so, wie sie das Datenprodukt material-overview/v2 liefert.
API_ROWS = [
    {"material_number": "MAT-1", "description": "Schraube", "material_group": "Rohstoffe",
     "plant_id": "W-KOE", "plant_name": "Werk Köln", "status": "Aktiv",
     "stock": 10, "price": 2.5, "stock_value": 25.0, "changed_on": "2026-01-01"},
    {"material_number": "MAT-2", "description": "Mutter", "material_group": None,
     "plant_id": "W-BER", "plant_name": "Werk Berlin", "status": "Gesperrt",
     "stock": None, "price": 1.0, "stock_value": None, "changed_on": "2026-02-01"},
]


def _envelope(rows: list[dict], **meta_over) -> dict:
    meta = {"product": "material-overview", "version": "3.0", "api_version": "v1",
            "generated_at": "2026-08-20T07:09:05Z", "row_count": len(rows),
            "total_count": len(rows), "source": "neo4j", "cache": "miss",
            "deprecated": False, "sunset": None}
    meta.update(meta_over)
    return {"meta": meta, "data": rows}


def _client(handler) -> DataProductClient:
    return DataProductClient(base_url="http://api.test",
                             transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _leerer_cache():
    """Jeder Test startet ohne Cache -- sonst faerben sie aufeinander ab."""
    repo._CACHE.clear()
    yield
    repo._CACHE.clear()


# --- Umformung (rein, ohne HTTP) -------------------------------------------

def test_plant_name_wird_zur_spalte_werk() -> None:
    """Die Uebersetzung an der Grenze API (englisch) <-> Tabelle (deutsch)."""
    frame = repo._rows_to_frame(API_ROWS)
    assert frame["werk"].to_list() == ["Werk Köln", "Werk Berlin"]
    assert "plant_name" not in frame.columns


def test_frame_hat_exakt_die_tabellenspalten() -> None:
    assert repo._rows_to_frame(API_ROWS).columns == COLUMNS


def test_unbekannte_api_felder_werden_ignoriert() -> None:
    """Ein neues Feld in der API darf das Dashboard NIE brechen.

    Genau deshalb ist ein hinzugefuegtes Feld nur eine Minor-Version.
    """
    rows = [dict(API_ROWS[0], brand_new_field="egal")]
    assert repo._rows_to_frame(rows).columns == COLUMNS


def test_fehlendes_feld_wird_zu_none_statt_absturz(caplog) -> None:
    rows = [{k: v for k, v in API_ROWS[0].items() if k != "stock_value"}]
    frame = repo._rows_to_frame(rows)
    assert frame["bestandswert"].to_list() == [None]
    assert "bestandswert" in caplog.text


def test_leeres_ergebnis_ergibt_schemakorrekten_frame() -> None:
    """Ohne Schema wuerde die Tabelle beim ersten leeren Ergebnis abstuerzen."""
    frame = repo._rows_to_frame([])
    assert frame.height == 0
    assert frame.columns == COLUMNS


def test_bestand_bleibt_none_und_wird_nicht_zu_null() -> None:
    """None heisst 'unbekannt', nicht 'kein Bestand'."""
    frame = repo._rows_to_frame(API_ROWS)
    assert frame["bestand"].to_list() == [10, None]


# --- Client über HTTP (gemockt) --------------------------------------------

def test_client_ruft_die_richtige_route_auf() -> None:
    gesehen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["url"] = str(request.url)
        return httpx.Response(200, json=_envelope(API_ROWS))

    rows, meta = _client(handler).fetch("material-overview", "v3", limit=50_000)
    assert gesehen["url"] == "http://api.test/api/v1/data-products/material-overview/v3?limit=50000"
    assert meta["version"] == "3.0"
    assert len(rows) == 2


def test_listenparameter_werden_wiederholt_angehaengt() -> None:
    """?status=Aktiv&status=Gesperrt -- genau das erwartet FastAPI."""
    gesehen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["query"] = str(request.url.query, "utf-8")
        return httpx.Response(200, json=_envelope([]))

    _client(handler).fetch("material-overview", "v3", status=["Aktiv", "Gesperrt"])
    assert gesehen["query"] == "status=Aktiv&status=Gesperrt"


def test_fehlerantwort_wird_zu_dataproducterror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"title": "Upstream data source unavailable",
                                         "detail": "Neo4j nicht erreichbar",
                                         "code": "upstream_unavailable"})

    with pytest.raises(DataProductError, match="Neo4j nicht erreichbar"):
        _client(handler).fetch("material-overview", "v3")


def test_nicht_erreichbare_api_wird_zu_dataproducterror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(DataProductError, match="unreachable"):
        _client(handler).fetch("material-overview", "v3")


# --- get_materials: Cache und Ausfallverhalten -----------------------------

def test_get_materials_liefert_dataframe(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_client",
                        _client(lambda r: httpx.Response(200, json=_envelope(API_ROWS))))
    frame = repo.get_materials()
    assert isinstance(frame, pl.DataFrame)
    assert frame.height == 2
    assert frame["werk"].to_list() == ["Werk Köln", "Werk Berlin"]


def test_zweiter_aufruf_kommt_aus_dem_cache(monkeypatch) -> None:
    aufrufe = []

    def handler(request: httpx.Request) -> httpx.Response:
        aufrufe.append(1)
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    repo.get_materials()
    repo.get_materials()
    assert len(aufrufe) == 1                  # kein zweiter HTTP-Roundtrip


def test_bei_api_ausfall_wird_der_letzte_stand_weitergeliefert(monkeypatch) -> None:
    """Ein Dashboard mit kurz veralteten Zahlen ist besser als ein leeres."""
    zustand = {"kaputt": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if zustand["kaputt"]:
            raise httpx.ConnectError("weg")
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    repo.get_materials()
    zustand["kaputt"] = True

    frame = repo.get_materials(force_reload=True)
    assert frame.height == 2                  # alter Stand statt Absturz


def test_ohne_cache_wird_der_fehler_durchgereicht(monkeypatch) -> None:
    """Keine stille leere Tabelle: der erste Fehlschlag muss auffallen."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")

    monkeypatch.setattr(repo, "_client", _client(handler))
    with pytest.raises(DataProductError):
        repo.get_materials()


def test_distinct_values_fuer_die_filter_dropdowns(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_client",
                        _client(lambda r: httpx.Response(200, json=_envelope(API_ROWS))))
    assert repo.distinct_values("werk") == ["Werk Berlin", "Werk Köln"]
    assert repo.distinct_values("warengruppe") == ["Rohstoffe"]   # None faellt raus


def test_kuerzung_wird_gemeldet(monkeypatch, caplog) -> None:
    """Eine vollständig aussehende, aber unvollständige Tabelle muss auffallen.

    Die API meldet 120.000 Zeilen, geliefert werden 50.000 (das serverseitige
    Maximum). Ohne diesen Hinweis zeigt das Dashboard eine plausible Tabelle mit
    fehlenden Daten -- und die KPI-Kacheln zählen ebenfalls zu wenig.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(API_ROWS, total_count=120_000))

    monkeypatch.setattr(repo, "_client", _client(handler))
    with caplog.at_level("ERROR"):
        repo.get_materials()

    assert repo.kuerzung() == (2, 120_000)
    assert "gekuerzt" in caplog.text.lower()


def test_ohne_kuerzung_kein_hinweis(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_client",
                        _client(lambda r: httpx.Response(200, json=_envelope(API_ROWS))))
    repo.get_materials()
    assert repo.kuerzung() is None


# --- Die Kopie darf nicht von der Vorlage abdriften -------------------------

def test_client_ist_mit_der_vorlage_deckungsgleich() -> None:
    """`data/api_client.py` ist eine Kopie von `api/.../dash_client.py`.

    Ohne diesen Test ist "ändert sie sich dort, wird sie hier nachgezogen" eine
    Absichtserklärung, die beim ersten Zeitdruck bricht.

    Verglichen wird der AST ohne Modul-Docstring: die beiden Dateien dürfen
    unterschiedliche Einleitungen und Kommentare haben (die Vorlage erklärt das
    Kopieren, die Kopie erklärt die Herkunft), aber kein unterschiedliches
    Verhalten.
    """
    import ast
    from pathlib import Path

    hier = Path(__file__).resolve()
    kopie = hier.parents[1] / "data" / "api_client.py"
    vorlage = hier.parents[3] / "api" / "src" / "clients" / "dash_client.py"

    if not vorlage.exists():                      # Dashboard ohne Monorepo ausgecheckt
        pytest.skip(f"Vorlage nicht gefunden: {vorlage}")

    def rumpf(pfad: Path) -> str:
        baum = ast.parse(pfad.read_text(encoding="utf-8"))
        knoten = baum.body
        if (knoten and isinstance(knoten[0], ast.Expr)
                and isinstance(knoten[0].value, ast.Constant)
                and isinstance(knoten[0].value.value, str)):
            knoten = knoten[1:]                   # Modul-Docstring weglassen
        return "\n".join(ast.dump(k, indent=2) for k in knoten)

    assert rumpf(kopie) == rumpf(vorlage), (
        "data/api_client.py und api/src/clients/dash_client.py sind "
        "auseinandergelaufen. Vorlage kopieren und nur den Kopf anpassen."
    )


# --- Anmeldung: Cache darf nicht ueber Nutzer hinweg lecken ------------------

def test_cache_ist_pro_rollen_satz_getrennt(monkeypatch) -> None:
    """Der Prozess-Cache darf keine Daten an Unberechtigte durchreichen.

    Ohne den Rollen-Schluessel wuerde der zweite Nutzer den Stand des ersten
    aus dem Cache bekommen -- die API waere nie gefragt worden und ihre 403
    damit nie gestellt. Das ist die Sorte Luecke, die kein Test der API selbst
    finden kann, weil sie im Dashboard sitzt.
    """
    aufrufe: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        aufrufe.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    monkeypatch.setattr(repo, "access_token", lambda: "token-planer")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset({"planner"}))
    repo.get_materials()
    repo.get_materials()                       # gecacht -> kein zweiter Aufruf
    assert len(aufrufe) == 1

    # Anderer Nutzer, andere Rollen -> eigener Eimer, also erneut zur API
    monkeypatch.setattr(repo, "access_token", lambda: "token-gast")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset({"guest"}))
    repo.get_materials()
    assert len(aufrufe) == 2


def test_das_token_wird_als_bearer_mitgeschickt(monkeypatch) -> None:
    """Ohne diesen Header antwortet die API mit 401 -- und zwar zu Recht."""
    gesehen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    monkeypatch.setattr(repo, "access_token", lambda: "abc.def.ghi")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset())
    repo.get_materials()
    assert gesehen == ["Bearer abc.def.ghi"]


def test_ein_403_wird_nicht_aus_dem_cache_beantwortet(monkeypatch) -> None:
    """Bei fehlender Berechtigung darf kein alter Stand ausgeliefert werden.

    Der Ausfall-Fallback ("lieber veraltete Zahlen als eine leere Tabelle")
    gilt fuer eine unerreichbare API -- nicht fuer eine, die bewusst Nein sagt.
    """
    antworten = [httpx.Response(200, json=_envelope(API_ROWS)),
                 httpx.Response(403, json={"title": "Access denied", "detail": "nope",
                                           "code": "forbidden"})]

    def handler(request: httpx.Request) -> httpx.Response:
        return antworten.pop(0)

    monkeypatch.setattr(repo, "_client", _client(handler))
    monkeypatch.setattr(repo, "access_token", lambda: "t")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset({"planner"}))

    repo.get_materials()                                   # fuellt den Cache
    with pytest.raises(NotAuthorisedError):
        repo.get_materials(force_reload=True)


# --- Invalidierung nach einem Schreibvorgang --------------------------------

def test_invalidate_erzwingt_beim_naechsten_zugriff_einen_neuen_abruf(monkeypatch) -> None:
    """Der API-Cache wird beim Schreiben serverseitig geleert -- der Cache hier
    nicht. Ohne diesen Aufruf sieht der Nutzer seine eigene Aenderung bis zu
    CACHE_TTL_SECONDS lang nicht."""
    abrufe = []

    def handler(request: httpx.Request) -> httpx.Response:
        abrufe.append(request.url.path)
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))

    repo.get_materials()
    repo.get_materials()
    assert len(abrufe) == 1, "der zweite Zugriff kam nicht aus dem Cache"

    repo.invalidate()
    repo.get_materials()
    assert len(abrufe) == 2


def test_invalidate_leert_die_eimer_aller_rollen(monkeypatch) -> None:
    """Schreibt jemand ein Mapping, betrifft das jeden -- nicht nur die Rollen
    des Schreibenden."""
    monkeypatch.setattr(repo, "_client",
                        _client(lambda request: httpx.Response(200, json=_envelope(API_ROWS))))

    monkeypatch.setattr(repo, "user_roles", lambda: ["planner"])
    repo.get_materials()
    monkeypatch.setattr(repo, "user_roles", lambda: ["viewer"])
    repo.get_materials()
    assert len(repo._CACHE) == 2

    repo.invalidate()
    assert repo._CACHE == {}
