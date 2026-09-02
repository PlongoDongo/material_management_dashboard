from __future__ import annotations


def test_healthz_reports_the_product_count(client):
    """Liveness checks ONLY the process -- never external systems.

    Otherwise a short Neo4j outage would restart every pod instead of merely
    producing errors.
    """
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data_products"] >= 3


def test_readyz_without_sources_reports_503(client_without_sources):
    """With no source configured the pod must NOT report itself ready.

    An API that accepts requests and then fails every one of them is worse than
    one that honestly takes itself out of the load balancer.
    """
    response = client_without_sources.get("/api/v1/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"neo4j": "not-configured", "postgres": "not-configured"}


def test_a_product_without_its_source_reports_a_configuration_error(client_without_sources):
    """No silent fallback dataset: if the source is missing, there is an error.

    That is why there is no sample data under src/.
    """
    response = client_without_sources.get("/api/v1/data-products/material-overview/v3")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "configuration_error"
    assert "NEO4J_URI" in body["detail"]


def test_the_request_id_header_is_set(client):
    response = client.get("/api/v1/healthz")
    assert response.headers["X-Request-ID"]
    assert "X-Response-Time-ms" in response.headers
