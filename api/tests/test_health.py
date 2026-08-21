from __future__ import annotations


def test_healthz_meldet_produktanzahl(client):
    """Liveness prueft NUR den Prozess -- niemals externe Systeme.

    Sonst wuerde ein kurzer Neo4j-Ausfall alle Pods neu starten lassen, statt
    nur Fehler zu liefern.
    """
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data_products"] >= 3


def test_readyz_ohne_datenquellen_meldet_503(client_ohne_datenquellen):
    """Ohne konfigurierte Quelle darf sich der Pod NICHT bereit melden.

    Eine API, die Requests annimmt und dann bei jedem scheitert, ist schlechter
    als eine, die sich ehrlich aus dem Loadbalancer nimmt.
    """
    response = client_ohne_datenquellen.get("/api/v1/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"neo4j": "not-configured", "postgres": "not-configured"}


def test_datenprodukt_ohne_datenquelle_meldet_konfigurationsfehler(client_ohne_datenquellen):
    """Kein stiller Ersatzdatensatz: fehlt die Quelle, gibt es einen Fehler.

    Das ist der Grund, warum es in src/ keine Beispieldaten gibt.
    """
    response = client_ohne_datenquellen.get("/api/v1/data-products/material-overview/v2")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "configuration_error"
    assert "NEO4J_URI" in body["detail"]


def test_request_id_header_wird_gesetzt(client):
    response = client.get("/api/v1/healthz")
    assert response.headers["X-Request-ID"]
    assert "X-Response-Time-ms" in response.headers
