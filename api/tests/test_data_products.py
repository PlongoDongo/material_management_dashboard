"""End-to-end over HTTP -- route, parameters, envelope, cache, versioning."""
from __future__ import annotations


def test_v2_returns_an_envelope_with_metadata(client):
    response = client.get("/api/v1/data-products/material-overview/v2")
    assert response.status_code == 200
    body = response.json()

    assert body["meta"]["product"] == "material-overview"
    assert body["meta"]["version"] == "2.1"      # full version in the metadata
    assert body["meta"]["source"] == "neo4j"
    assert body["meta"]["row_count"] == len(body["data"])
    assert body["meta"]["total_count"] == 64
    assert body["data"][0]["material_number"].startswith("MAT-")


def test_v2_and_v3_have_different_contracts(client):
    v2 = client.get("/api/v1/data-products/material-overview/v2").json()["data"][0]
    v3 = client.get("/api/v1/data-products/material-overview/v3").json()["data"][0]

    assert "plant" in v2 and "unit" in v2
    assert "plant" not in v3 and "unit" not in v3
    assert {"plant_id", "plant_name", "stock_value"} <= set(v3)


def test_a_deprecated_version_sets_the_right_headers(client):
    response = client.get("/api/v1/data-products/material-overview/v2")
    assert response.headers["Deprecation"] == "true"
    assert "2026" in response.headers["Sunset"]
    assert response.headers["X-Data-Product-Version"] == "2.1"
    assert response.json()["meta"]["deprecated"] is True


def test_the_latest_alias_points_at_v3(client):
    latest = client.get("/api/v1/data-products/material-overview/latest").json()
    assert latest["meta"]["version"] == "3.0"


def test_filters_are_applied_server_side(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v3",
        params={"status": ["Gesperrt"]},
    )
    rows = response.json()["data"]
    assert rows, "the fixture should contain blocked materials"
    assert {row["status"] for row in rows} == {"Gesperrt"}


def test_multi_select_via_repeated_query_parameters(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v3",
        params={"status": ["Gesperrt", "Obsolet"]},
    )
    assert {row["status"] for row in response.json()["data"]} == {"Gesperrt", "Obsolet"}


def test_pagination_reports_the_total(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v3", params={"limit": 10, "offset": 5}
    )
    meta = response.json()["meta"]
    assert meta["row_count"] == 10
    assert meta["total_count"] == 64


def test_an_unknown_parameter_returns_422_instead_of_being_ignored(client):
    """The typo test: ?stauts=Aktiv must NOT return every row."""
    response = client.get(
        "/api/v1/data-products/material-overview/v3", params={"stauts": "Aktiv"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_an_out_of_range_value_is_rejected(client):
    response = client.get(
        "/api/v1/data-products/material-overview/v3", params={"limit": 0}
    )
    assert response.status_code == 422


def test_the_cache_hits_on_the_second_call(client):
    path = "/api/v1/data-products/supplier-risk/v2"
    assert client.get(path).json()["meta"]["cache"] == "miss"
    assert client.get(path).json()["meta"]["cache"] == "hit"


def test_different_parameters_are_different_cache_entries(client):
    path = "/api/v1/data-products/material-overview/v3"
    client.get(path, params={"status": ["Aktiv"]})
    second = client.get(path, params={"status": ["Gesperrt"]})
    assert second.json()["meta"]["cache"] == "miss"
    assert {r["status"] for r in second.json()["data"]} == {"Gesperrt"}


def test_an_etag_yields_304_without_a_body(client):
    path = "/api/v1/data-products/material-overview/v3"
    first = client.get(path)
    second = client.get(path, headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 304
    assert not second.content


def test_the_cross_source_product_joins_both_sources(client):
    body = client.get("/api/v1/data-products/supplier-risk/v2").json()
    # meta.source shows which sources actually fed the response
    assert body["meta"]["source"] == "neo4j+postgres"
    rows = body["data"]
    assert len(rows) == 4
    # sorted by risk, descending
    scores = [row["risk_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    # master data (graph side) and metrics (SQL side) are both present
    assert rows[0]["material_count"] > 0
    assert rows[0]["deliveries"] > 0


def test_openapi_contains_each_product_with_its_own_schema(client):
    """The proof that the generated routes are typed."""
    spec = client.get("/openapi.json").json()
    assert "/api/v1/data-products/material-overview/v3" in spec["paths"]
    assert "/api/v1/data-products/supplier-risk/v2" in spec["paths"]
    assert "MaterialRowV3" in spec["components"]["schemas"]
    assert "SupplierRiskRow" in spec["components"]["schemas"]


def test_a_write_endpoint_invalidates_the_cache(client):
    path = "/api/v1/data-products/material-overview/v3"
    client.get(path)
    assert client.get(path).json()["meta"]["cache"] == "hit"

    created = client.post(
        "/api/v1/mappings",
        json={"material_number": "MAT-100777", "target_material_group": "Rohstoffe"},
    )
    assert created.status_code == 201

    assert client.get(path).json()["meta"]["cache"] == "miss"


def test_a_filter_is_passed_to_the_query_as_a_parameter(client, fake_sources):
    """Filters that live in the query are passed through as parameters.

    The fake deliberately does NOT apply the filter -- it would otherwise
    reimplement Cypher in Python and the test would end up checking the fake.
    What is checked here is the seam: does `country` reach the CYPHER query, and
    does the endpoint still return something valid? Whether the filter actually
    filters is checked by tests/test_integration_neo4j.py against a real
    database.
    """
    response = client.get("/api/v1/data-products/supplier-risk/v2",
                          params={"country": ["DE", "AT"]})
    assert response.status_code == 200          # otherwise a 500 looks like success
    assert response.json()["meta"]["version"] == "2.0"

    cypher, parameters = fake_sources.calls[0]
    assert "s.land IN $country" in cypher       # the value went to THIS query
    assert parameters["country"] == ["DE", "AT"]


def test_without_a_filter_none_is_passed(client, fake_sources):
    """`$country IS NULL OR ...` -- with no filter the condition drops out."""
    response = client.get("/api/v1/data-products/supplier-risk/v2")
    assert response.status_code == 200
    assert fake_sources.calls[0][1]["country"] is None


def test_an_empty_list_counts_as_no_filter(client, fake_sources):
    """An empty multi-select must not filter everything away.

    `[] IS NULL` is false in Cypher and so is `x IN []` -- without
    normalisation the result would be zero rows, with no error and no hint.
    """
    response = client.get("/api/v1/data-products/supplier-risk/v2", params={"country": []})
    assert response.status_code == 200
    assert fake_sources.calls[0][1]["country"] is None


def test_the_filter_reaches_the_second_source_too(client, fake_sources):
    """The filter must not shrink only the cheap source.

    The delivery history is the table that grows over time -- it is narrowed to
    the suppliers the graph query left over.
    """
    client.get("/api/v1/data-products/supplier-risk/v2", params={"country": ["DE"]})

    cypher, cypher_parameters = fake_sources.calls[0]
    sql, sql_parameters = fake_sources.calls[1]
    assert "supplier_id = ANY(:ids)" in sql
    assert sql_parameters["ids"] == ["L-001", "L-002", "L-003", "L-004"]
    assert "since" in sql_parameters and "since" not in cypher_parameters
