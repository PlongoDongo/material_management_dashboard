"""
Health and readiness endpoints.

The distinction matters operationally:

    /healthz   "The process is alive."  -> otherwise Kubernetes restarts it.
               Checks NOTHING external. Otherwise a short Neo4j outage would
               kill every pod instead of merely producing errors.

    /readyz    "I can answer requests."  -> otherwise the load balancer takes
               the pod out of rotation. Checks the data sources.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

from data_api import __version__
from data_api.api.deps import SettingsDep
from data_api.products.introspect import required_sources
from data_api.products.registry import registry

router = APIRouter(tags=["Operations"])


@router.get("/healthz", summary="Liveness -- checks the process only")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "data_products": len(registry)}


@router.get("/readyz", summary="Readiness -- checks the data sources")
async def readyz(request: Request, settings: SettingsDep, response: Response) -> dict[str, Any]:
    # Only check what a data product actually queries. A deployment without
    # Postgres that only serves graph products is ready -- insisting on both
    # sources would keep the pod out of the load balancer forever.
    needed = required_sources()
    checks: dict[str, str] = {}

    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        checks["neo4j"] = "not-configured"
    else:
        try:
            await driver.verify_connectivity()
            checks["neo4j"] = "ok"
        except Exception as exc:                      # noqa: BLE001
            checks["neo4j"] = f"error: {type(exc).__name__}"

    engine = getattr(request.app.state, "sql_engine", None)
    if engine is None:
        checks["postgres"] = "not-configured"
    else:
        try:
            from sqlalchemy import text

            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception as exc:                      # noqa: BLE001
            checks["postgres"] = f"error: {type(exc).__name__}"

    # A source that is not configured is just as bad as a broken one -- but only
    # if a product needs it. The pod then reports "not ready" instead of
    # accepting requests it cannot serve.
    degraded = [name for name, state in checks.items()
                if name in needed and state != "ok"]

    if degraded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "degraded" if degraded else "ready",
        "env": settings.api_env,
        "required": sorted(needed),
        "checks": checks,
    }
