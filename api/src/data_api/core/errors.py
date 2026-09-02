"""
Error handling: ONE error format for the whole API.

The format is RFC 9457 "Problem Details" (application/problem+json):

    {"type": "about:blank", "title": "Data source unavailable",
     "status": 503, "detail": "...", "code": "upstream_unavailable",
     "request_id": "3f2a..."}

Why this matters: the Dash callbacks need ONE path for error handling. If the
API sometimes returns `{"detail": ...}`, sometimes `{"error": ...}` and an HTML
stack trace on a Neo4j timeout, that logic gets rewritten in every dashboard.

The rule in code: NEVER `raise HTTPException(...)` in the domain layer. Raise an
`AppError` subclass instead -- those know nothing about HTTP and are therefore
testable without a web server. The translation to HTTP happens right here.
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
    """Base class of all domain errors. Deliberately knows nothing about FastAPI."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    title: str = "Internal server error"

    def __init__(self, detail: str = "", **extra: object) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra


class ForbiddenError(AppError):
    """The caller is known but is not allowed to see this data product.

    Replaces a `raise HTTPException(403, ...)` in the router: this way the 403
    also carries a `code` a dashboard can check for, and the rule "no
    HTTPException in the domain layer" holds without exceptions.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    title = "Access denied"


class UpstreamUnavailableError(AppError):
    """A data source (Neo4j/Postgres) is unreachable -> 503, not 500.

    The distinction matters to the dashboards: 503 means "try again later",
    500 means "this is a bug, please report it".
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
    # Request object first, ContextVar second: on a 500 this handler runs
    # outside RequestContextMiddleware, whose `finally` has already reset the
    # ContextVar.
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
        # Also as a header: on a 500 the response no longer passes through the
        # middleware that would otherwise set it.
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
            "The request parameters are invalid.",
            "validation_error",
        )
        # Attach the field-level errors -- helps when debugging Dash callbacks.
        import json

        body = json.loads(response.body)
        body["errors"] = json.loads(json.dumps(exc.errors(), default=str))
        return JSONResponse(status_code=422, media_type="application/problem+json",
                            content=body, headers=dict(response.headers))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error: %s", exc)
        return _problem(request, 500, "Internal server error",
                        "Unexpected error.", "internal_error")
