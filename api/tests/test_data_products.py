"""Ende-zu-Ende ueber HTTP -- Route, Parameter, Umschlag, Cache, Versionierung."""
from __future__ import annotations


def test_v1_liefert_umschlag_mit_metadaten(client):
    response = client.get("/api/v1/data-products/material-overview/v1")
    assert response.status_code == 200
    body = response.json()

    assert body["meta"]["product"] == "material-overview"
    assert body["meta"]["version"] == "1.2"      # volle Version in den Metadaten
    assert body["meta"]["source"] == "neo4j"   # FakeSources meldet die echte Quelle
    assert body["meta"]["row_count"] == len(body["data"])
    assert body["meta"]["total_count"] == 64
    assert body["data"][0]["material_nr"].startswith("MAT-")


def test_v1_und_v2_haben_unterschiedliche_vertraege(client):
    v1 = client.get("/api/v1/data-products/material-overview/v1").json()["data"][0]
    v2 = client.get("/api/v1/data-products/material-overview/v2").json()["data"][0]

    assert "werk" in v1 and "einheit" in v1
    assert "werk" not in v2 and "einheit" not in v2
    assert {"werk_id", "werk_name", "bestandswert"} <= set(v2)


def test_deprecated_version_setzt_die_richtigen_header(client):
    response = client.get("/api/v1/data-products/material-overview/v1")
    assert response.headers["Deprecation"] == "true"
    assert "2026" in response.headers["Sunset"]
    assert response.headers["X-Data-Product-Version"] == "1.2"
    assert response.json()["meta"]["deprecated"] is True


def test_latest_alias_zeigt_auf_v2(client):
    latest = client.get("/api/v1/data-products/material-overview/latest").json()
    assert latest["meta"]["version"] == "2.0"


def test_filter_werden_serverseitig_angewandt(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v2",
        params={"status": ["Gesperrt"]},
    )
    daten = response.json()["data"]
    assert daten, "Fixture sollte gesperrte Materialien enthalten"
    assert {row["status"] for row in daten} == {"Gesperrt"}


def test_mehrfachauswahl_ueber_wiederholte_query_parameter(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v2",
        params={"status": ["Gesperrt", "Obsolet"]},
    )
    assert {row["status"] for row in response.json()["data"]} == {"Gesperrt", "Obsolet"}


def test_paginierung_meldet_gesamtzahl(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v2", params={"limit": 10, "offset": 5}
    )
    meta = response.json()["meta"]
    assert meta["row_count"] == 10
    assert meta["total_count"] == 64


def test_unbekannter_parameter_liefert_422_statt_stiller_ignoranz(client):
    """Der Tippfehler-Test: ?stauts=Aktiv darf NICHT alle Zeilen liefern."""
    response = client.get(
        "/api/v1/data-products/material-overview/v2", params={"stauts": "Aktiv"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_ungueltiger_wertebereich_wird_abgefangen(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v2", params={"limit": 0}
    )
    assert response.status_code == 422


def test_cache_greift_beim_zweiten_aufruf(client):
    pfad = "/api/v1/data-products/supplier-risk/v1"
    assert client.get(pfad).json()["meta"]["cache"] == "miss"
    assert client.get(pfad).json()["meta"]["cache"] == "hit"


def test_unterschiedliche_parameter_sind_unterschiedliche_cache_eintraege(client):
    pfad = "/api/v1/data-products/material-overview/v2"
    client.get(pfad, params={"status": ["Aktiv"]})
    zweite = client.get(pfad, params={"status": ["Gesperrt"]})
    assert zweite.json()["meta"]["cache"] == "miss"
    assert {r["status"] for r in zweite.json()["data"]} == {"Gesperrt"}


def test_etag_liefert_304_ohne_body(client):
    pfad = "/api/v1/data-products/material-overview/v2"
    erste = client.get(pfad)
    zweite = client.get(pfad, headers={"If-None-Match": erste.headers["ETag"]})
    assert zweite.status_code == 304
    assert not zweite.content


def test_cross_source_produkt_verknuepft_beide_quellen(client):
    body = client.get("/api/v1/data-products/supplier-risk/v1").json()
    # meta.source zeigt, welche Quellen die Antwort tatsaechlich gespeist haben
    assert body["meta"]["source"] == "neo4j+postgres"
    zeilen = body["data"]
    assert len(zeilen) == 4
    # Nach Risiko absteigend sortiert
    scores = [row["risiko_score"] for row in zeilen]
    assert scores == sorted(scores, reverse=True)
    # Stammdaten (Neo4j-Seite) und Kennzahlen (SQL-Seite) sind beide da
    assert zeilen[0]["anzahl_materialien"] > 0
    assert zeilen[0]["lieferungen"] > 0


def test_openapi_enthaelt_jedes_produkt_mit_eigenem_schema(client):
    """Der Beweis, dass die generierten Routen typisiert sind."""
    spec = client.get("/openapi.json").json()
    assert "/api/v1/data-products/material-overview/v2" in spec["paths"]
    assert "/api/v1/data-products/supplier-risk/v1" in spec["paths"]
    assert "MaterialRowV2" in spec["components"]["schemas"]
    assert "SupplierRiskRow" in spec["components"]["schemas"]


def test_schreibender_endpunkt_invalidiert_den_cache(client):
    pfad = "/api/v1/data-products/material-overview/v2"
    client.get(pfad)
    assert client.get(pfad).json()["meta"]["cache"] == "hit"

    angelegt = client.post(
        "/api/v1/mappings",
        json={"material_nr": "MAT-100777", "ziel_warengruppe": "Rohstoffe"},
    )
    assert angelegt.status_code == 201

    assert client.get(pfad).json()["meta"]["cache"] == "miss"


def test_filter_wird_als_parameter_an_die_abfrage_uebergeben(client, fake_sources):
    """Filter, die in der Abfrage stehen, werden als Parameter durchgereicht.

    Der Fake wendet den Filter bewusst NICHT an -- er wuerde sonst Cypher in
    Python nachbauen, und der Test pruefte am Ende den Fake statt die API.
    Geprueft wird die Nahtstelle: kommt `land` an der CYPHER-Abfrage an, und
    liefert der Endpunkt trotzdem eine gueltige Antwort? Ob der Filter richtig
    filtert, prueft tests/test_integration_neo4j.py gegen eine echte Datenbank.
    """
    antwort = client.get("/api/v1/data-products/supplier-risk/v1",
                         params={"land": ["DE", "AT"]})
    assert antwort.status_code == 200          # sonst sieht ein 500er wie Erfolg aus
    assert antwort.json()["meta"]["version"] == "1.2"

    cypher, parameter = fake_sources.aufrufe[0]
    assert "s.land IN $land" in cypher         # der Wert ging an DIESE Abfrage
    assert parameter["land"] == ["DE", "AT"]


def test_ohne_filter_wird_none_uebergeben(client, fake_sources):
    """`$land IS NULL OR ...` -- ohne Filter faellt die Bedingung im Cypher weg."""
    antwort = client.get("/api/v1/data-products/supplier-risk/v1")
    assert antwort.status_code == 200
    assert fake_sources.aufrufe[0][1]["land"] is None


def test_leere_liste_gilt_als_kein_filter(client, fake_sources):
    """Ein leeres Mehrfach-Auswahlfeld darf nicht alles wegfiltern.

    `[] IS NULL` ist in Cypher false und `x IN []` ebenfalls -- ohne
    Normalisierung kaeme null Zeilen zurueck, ohne Fehler und ohne Hinweis.
    """
    antwort = client.get("/api/v1/data-products/supplier-risk/v1", params={"land": []})
    assert antwort.status_code == 200
    assert fake_sources.aufrufe[0][1]["land"] is None


def test_filter_erreicht_auch_die_zweite_quelle(client, fake_sources):
    """Der Filter darf nicht nur die billige Quelle verkleinern.

    Die Lieferhistorie ist die Tabelle, die mit der Zeit waechst -- sie wird auf
    die Lieferanten eingegrenzt, die die Graph-Abfrage uebrig gelassen hat.
    """
    client.get("/api/v1/data-products/supplier-risk/v1", params={"land": ["DE"]})

    cypher, cypher_parameter = fake_sources.aufrufe[0]
    sql, sql_parameter = fake_sources.aufrufe[1]
    assert "lieferant_id = ANY(:ids)" in sql
    assert sql_parameter["ids"] == ["L-001", "L-002", "L-003", "L-004"]
    assert "seit" in sql_parameter and "seit" not in cypher_parameter
