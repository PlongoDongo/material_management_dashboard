"""
Health- und Readiness-Endpunkte.

Der Unterschied ist betrieblich wichtig:

    /healthz   "Der Prozess lebt."     -> Kubernetes startet ihn sonst neu.
               Prueft NICHTS Externes. Sonst killt ein kurzer Neo4j-Ausfall
               alle Pods, statt nur Fehler zu liefern.

    /readyz    "Ich kann Anfragen beantworten." -> Loadbalancer nimmt den Pod
               sonst aus dem Verkehr. Prueft die Datenquellen.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

from data_api import __version__
from data_api.api.deps import SettingsDep
from data_api.products.introspect import required_sources
from data_api.products.registry import registry

router = APIRouter(tags=["Betrieb"])


@router.get("/healthz", summary="Liveness -- prueft nur den Prozess")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "data_products": len(registry)}


@router.get("/readyz", summary="Readiness -- prueft die Datenquellen")
async def readyz(request: Request, settings: SettingsDep, response: Response) -> dict[str, Any]:
    # Nur pruefen, was ein Datenprodukt tatsaechlich abfragt. Ein Deployment
    # ohne Postgres, das nur Graph-Produkte ausliefert, ist bereit -- wuerde man
    # stur beide Quellen verlangen, kaeme der Pod nie in den Loadbalancer.
    benoetigt = required_sources()
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

    # Eine nicht konfigurierte Quelle ist genauso schlimm wie eine kaputte:
    # Datenprodukte, die sie brauchen, koennen nicht antworten. Der Pod meldet
    # sich deshalb nicht bereit, statt Requests anzunehmen und zu scheitern.
    degraded = [name for name, state in checks.items()
                if name in benoetigt and state != "ok"]

    if degraded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "degraded" if degraded else "ready",
        "env": settings.api_env,
        "required": sorted(benoetigt),
        "checks": checks,
    }
