"""
Fehlerbehandlung: EIN Fehlerformat fuer die ganze API.

Format ist RFC 9457 "Problem Details" (application/problem+json):

    {"type": "about:blank", "title": "Data product not found",
     "status": 404, "detail": "...", "code": "product_not_found",
     "request_id": "3f2a..."}

Warum das wichtig ist: die Dash-Callbacks brauchen EINEN Pfad fuer
Fehlerbehandlung. Wenn FastAPI mal `{"detail": ...}`, mal `{"error": ...}` und
bei einem Neo4j-Timeout einen HTML-Stacktrace liefert, steht diese Logik in
jedem Dashboard neu.

Regel im Code: NIEMALS `raise HTTPException(...)` in der Domaenenschicht.
Dort wird eine `AppError`-Unterklasse geworfen -- die kennt kein HTTP und ist
damit ohne Webserver testbar. Die Uebersetzung nach HTTP passiert genau hier.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from data_api.core.logging import request_id_var

log = logging.getLogger(__name__)


class AppError(Exception):
    """Basisklasse aller fachlichen Fehler. Kennt bewusst kein FastAPI."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    title: str = "Internal server error"

    def __init__(self, detail: str = "", **extra: object) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra


class ForbiddenError(AppError):
    """Aufrufer ist bekannt, darf dieses Datenprodukt aber nicht sehen.

    Ersetzt ein `raise HTTPException(403, ...)` im Router: so bekommt auch der
    403 einen `code`, auf den ein Dashboard pruefen kann, und die Regel
    "in der Domaenenschicht keine HTTPException" gilt ohne Ausnahme.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    title = "Access denied"


class UpstreamUnavailableError(AppError):
    """Datenquelle (Neo4j/Postgres) nicht erreichbar -> 503, nicht 500.

    Unterschied ist fuer die Dashboards relevant: 503 heisst "spaeter nochmal",
    500 heisst "Bug, bitte melden".
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "upstream_unavailable"
    title = "Upstream data source unavailable"


class ConfigurationError(AppError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "configuration_error"
    title = "Server misconfigured"


def _problem(
    request: Request, status_code: int, title: str, detail: str, code: str
) -> JSONResponse:
    # Erst das Request-Objekt, dann der ContextVar: bei einem 500er laeuft
    # dieser Handler ausserhalb der RequestContextMiddleware, deren `finally`
    # den ContextVar bereits zurueckgesetzt hat.
    request_id = getattr(request.state, "request_id", None) or request_id_var.get()
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "detail": detail,
            "code": code,
            "request_id": request_id,
        },
        # Auch als Header: bei einem 500er kommt die Antwort nicht mehr durch
        # die RequestContextMiddleware, die ihn sonst setzt.
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.exception("AppError: %s", exc.detail)
        return _problem(request, exc.status_code, exc.title, exc.detail, exc.code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(request, exc.status_code, "HTTP error", str(exc.detail), "http_error")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        response = _problem(
            request,
            422,
            "Invalid request",
            "Die Anfrageparameter sind ungueltig.",
            "validation_error",
        )
        # Feldgenaue Fehler anhaengen -- hilft beim Debuggen der Dash-Callbacks.
        import json

        body = json.loads(response.body)
        body["errors"] = json.loads(json.dumps(exc.errors(), default=str))
        return JSONResponse(status_code=422, media_type="application/problem+json",
                            content=body, headers=dict(response.headers))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unbehandelter Fehler: %s", exc)
        return _problem(request, 500, "Internal server error",
                        "Unerwarteter Fehler.", "internal_error")
