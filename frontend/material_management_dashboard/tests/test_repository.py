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
from data.api_client import DataProductClient, DataProductError
from data.schema import COLUMNS

# Zeilen genau so, wie sie das Datenprodukt material-overview/v2 liefert.
API_ROWS = [
    {"material_nr": "MAT-1", "bezeichnung": "Schraube", "warengruppe": "Rohstoffe",
     "werk_id": "W-KOE", "werk_name": "Werk Köln", "status": "Aktiv",
     "bestand": 10, "preis": 2.5, "bestandswert": 25.0, "geaendert": "2026-01-01"},
    {"material_nr": "MAT-2", "bezeichnung": "Mutter", "warengruppe": None,
     "werk_id": "W-BER", "werk_name": "Werk Berlin", "status": "Gesperrt",
     "bestand": None, "preis": 1.0, "bestandswert": None, "geaendert": "2026-02-01"},
]


def _envelope(rows: list[dict], **meta_over) -> dict:
    meta = {"product": "material-overview", "version": "2.0", "api_version": "v1",
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

def test_werk_name_wird_zur_spalte_werk() -> None:
    """Die eine echte Umbenennung an der Grenze API <-> Dashboard."""
    frame = repo._rows_to_frame(API_ROWS)
    assert frame["werk"].to_list() == ["Werk Köln", "Werk Berlin"]
    assert "werk_name" not in frame.columns


def test_frame_hat_exakt_die_tabellenspalten() -> None:
    assert repo._rows_to_frame(API_ROWS).columns == COLUMNS


def test_unbekannte_api_felder_werden_ignoriert() -> None:
    """Ein neues Feld in der API darf das Dashboard NIE brechen.

    Genau deshalb ist ein hinzugefuegtes Feld nur eine Minor-Version.
    """
    rows = [dict(API_ROWS[0], voellig_neues_feld="egal")]
    assert repo._rows_to_frame(rows).columns == COLUMNS


def test_fehlendes_feld_wird_zu_none_statt_absturz(caplog) -> None:
    rows = [{k: v for k, v in API_ROWS[0].items() if k != "bestandswert"}]
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

    rows, meta = _client(handler).fetch("material-overview", "v2", limit=50_000)
    assert gesehen["url"] == "http://api.test/api/v1/data-products/material-overview/v2?limit=50000"
    assert meta["version"] == "2.0"
    assert len(rows) == 2


def test_listenparameter_werden_wiederholt_angehaengt() -> None:
    """?status=Aktiv&status=Gesperrt -- genau das erwartet FastAPI."""
    gesehen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["query"] = str(request.url.query, "utf-8")
        return httpx.Response(200, json=_envelope([]))

    _client(handler).fetch("material-overview", "v2", status=["Aktiv", "Gesperrt"])
    assert gesehen["query"] == "status=Aktiv&status=Gesperrt"


def test_fehlerantwort_wird_zu_dataproducterror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"title": "Upstream data source unavailable",
                                         "detail": "Neo4j nicht erreichbar",
                                         "code": "upstream_unavailable"})

    with pytest.raises(DataProductError, match="Neo4j nicht erreichbar"):
        _client(handler).fetch("material-overview", "v2")


def test_nicht_erreichbare_api_wird_zu_dataproducterror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(DataProductError, match="nicht erreichbar"):
        _client(handler).fetch("material-overview", "v2")


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
