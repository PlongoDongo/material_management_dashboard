from __future__ import annotations


def test_katalog_listet_alle_produkte(client):
    response = client.get("/api/v1/catalog")
    assert response.status_code == 200
    namen = {entry["name"] for entry in response.json()}
    assert {"material-overview", "supplier-risk"} <= namen


def test_katalog_zeigt_beide_versionen_und_das_sunset_datum(client):
    entry = client.get("/api/v1/catalog/material-overview").json()
    versionen = {v["version"]: v for v in entry["versions"]}
    assert set(versionen) == {"1.2", "2.0"}
    assert versionen["1.2"]["deprecated"] is True
    assert versionen["1.2"]["sunset"] == "2026-12-31"
    assert versionen["2.0"]["deprecated"] is False
    # latest ueberspringt deprecated Versionen
    assert entry["latest"] == "2.0"


def test_katalog_meldet_die_vertragsfelder(client):
    entry = client.get("/api/v1/catalog/material-overview").json()
    v2 = next(v for v in entry["versions"] if v["version"] == "2.0")
    assert "bestandswert" in v2["fields"]
    assert "werk_id" in v2["fields"]


def test_unbekanntes_produkt_liefert_problem_details(client):
    response = client.get("/api/v1/catalog/gibt-es-nicht")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "http_error"
