"""
Health and readiness endpoints.

    /healthz   "The process is alive." Checks nothing external, so a short
               database outage does not get the process restarted.
    /readyz    "Both data sources are reachable." The check to run after a
               deployment; a 503 names the source that is missing or broken.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from api.deps import SettingsDep
from core.config import __version__
from products.registry import registry

router = APIRouter(tags=["Operations"])


@router.get("/healthz", summary="Liveness -- checks the process only")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "data_products": len(registry)}


@router.get("/readyz", summary="Readiness -- checks the data sources")
async def readyz(request: Request, settings: SettingsDep, response: Response) -> dict[str, Any]:
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
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception as exc:                      # noqa: BLE001
            checks["postgres"] = f"error: {type(exc).__name__}"

    degraded = [name for name, state in checks.items() if state != "ok"]
    if degraded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "degraded" if degraded else "ready",
        "env": settings.api_env,
        "checks": checks,
    }
