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

import json
import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging import request_id_var

log = logging.getLogger(__name__)


class AppError(Exception):
    """Base class of all domain errors. Deliberately knows nothing about FastAPI.

    An unhandled exception becomes a 500 anyway -- this exists for every error
    that should NOT be one. A subclass sets the status (503, 409, 403, ...) and
    a stable `code` the dashboard can branch on; db/ and products/ raise it
    without importing FastAPI, and `register_exception_handlers` below turns it
    into the response.
    """

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


class UnauthorizedError(AppError):
    """No token, or not a valid one -- 401.

    401 means "authenticate (again)", 403 means "authenticated, but not for
    this". The dashboard branches on exactly that difference: 401 sends the
    user back through the Keycloak login, 403 shows "you lack the role".
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    title = "Authentication required"


class UpstreamUnavailableError(AppError):
    """A data source (Neo4j/Postgres) is unreachable -> 503, not 500.

    The distinction matters to the dashboards: 503 means "try again later",
    500 means "this is a bug, please report it".
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "upstream_unavailable"
    title = "Upstream data source unavailable"


class ConflictError(AppError):
    """The write contradicts data that already exists -- a duplicate key, a
    violated constraint -> 409.

    Unlike a 503, retrying will not help: the input has to change. db/sources.py
    raises it for Neo4j's ConstraintError and SQL's IntegrityError.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    title = "Conflict with existing data"


class ConfigurationError(AppError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "configuration_error"
    title = "Server misconfigured"


def _problem(
    request: Request,
    status_code: int,
    title: str,
    detail: str,
    code: str,
    **extra: object,
) -> JSONResponse:
    """Builds the one and only error response shape of this API.

    `extra` becomes additional top-level members of the problem document --
    RFC 9457 calls those "extension members" and explicitly allows them. Used
    by the validation handler for its `errors` list. Values must be
    JSON-serialisable; the caller is responsible for that.
    """
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
            **extra,
        },
        # Also as a header: on a 500 the response no longer passes through the
        # middleware that would otherwise set it.
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Makes every error leave the API in the one shape `_problem` builds.

    What FastAPI answers WITHOUT these handlers:

        AppError                plain-text 500 -- the subclass's 503/409 is lost
        HTTPException           {"detail": "..."} -- no code, no request id
        RequestValidationError  422 {"detail": [...]} -- a list, where every
                                other error has a string
        Exception               plain-text 500 -- no JSON, no request id, which
                                is the one case where the id is needed most

    One registration per base class is enough: the lookup walks the exception's
    class hierarchy, so a new AppError subclass needs nothing here.
    """
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if HTTPStatus(exc.status_code).is_server_error:
            log.error("AppError: %s", exc.detail, exc_info=exc)
        return _problem(request, exc.status_code, exc.title, exc.detail, exc.code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(request, exc.status_code, "HTTP error", str(exc.detail), "http_error")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            request,
            422,
            "Invalid request",
            "The request parameters are invalid.",
            "validation_error",
            # The field-level errors -- helps when debugging Dash callbacks.
            # Round-tripped through `default=str` because `ctx` can carry
            # exception objects that json.dumps refuses: an error handler that
            # raises on its own is the worst possible failure mode here.
            errors=json.loads(json.dumps(exc.errors(), default=str)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("Unhandled error: %s", exc, exc_info=exc)
        return _problem(request, 500, "Internal server error",
                        "Unexpected error.", "internal_error")
