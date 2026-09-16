from __future__ import annotations

from fastapi.testclient import TestClient


def test_catalog_lists_every_product(client: TestClient) -> None:
    response = client.get("/api/v1/catalog")
    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()}
    assert {"material-overview", "supplier-risk"} <= names


def test_catalog_shows_both_versions_and_the_sunset_date(client: TestClient) -> None:
    entry = client.get("/api/v1/catalog/material-overview").json()
    versions = {v["version"]: v for v in entry["versions"]}
    assert set(versions) == {"2.1", "3.0"}
    assert versions["2.1"]["deprecated"] is True
    assert versions["2.1"]["sunset"] == "2026-12-31"
    assert versions["3.0"]["deprecated"] is False
    # latest skips deprecated versions
    assert entry["latest"] == "3.0"


def test_catalog_reports_the_contract_fields(client: TestClient) -> None:
    entry = client.get("/api/v1/catalog/material-overview").json()
    v3 = next(v for v in entry["versions"] if v["version"] == "3.0")
    assert "stock_value" in v3["fields"]
    assert "plant_id" in v3["fields"]


def test_an_unknown_product_returns_problem_details(client: TestClient) -> None:
    response = client.get("/api/v1/catalog/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    # `not_found`, not the generic `http_error`: the catalog raises NotFoundError
    # itself, so a dashboard can tell "no such product" from any other 404.
    assert response.json()["code"] == "not_found"
