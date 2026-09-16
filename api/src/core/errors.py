"""
Error handling: ONE error format for the whole API.

The format is RFC 9457 "Problem Details" (application/problem+json):

    {"type": "about:blank", "title": "Data source unavailable",
     "status": 503, "detail": "...", "code": "upstream_unavailable",
     "request_id": "3f2a..."}

Why this matters: the Dash callbacks need ONE path for error handling. If the
API sometimes returns `{"detail": ...}`, sometimes `{"error": ...}` and an HTML
stack trace on a Neo4j timeout, that logic gets rewritten in every dashboard.

The rule in code: NEVER `raise HTTPException(...)`. Raise an `AppError` subclass
instead -- those know nothing about HTTP and are therefore testable without a
web server. The translation to HTTP happens right here.

ONE PLACE PER FACT
==================
Everything about an error lives on its class: the status code, the `code` a
dashboard branches on, and the `title`, which is also its description in /docs.

    class ConflictError(AppError):        -> 409, code "conflict", and the
        status_code = 409                    heading Swagger shows for it
        code = "conflict"
        title = "Conflict with existing data"

`documented_errors(...)` reads those classes for the OpenAPI schema, and
`_problem()` builds the response body from the same `Problem` model that /docs
shows. Adding an error type therefore means writing the class and naming it on
the routes that can answer it -- nothing else, and there is no second table of
descriptions to keep in step.
"""
from __future__ import annotations

import json
import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging import request_id_var

log = logging.getLogger(__name__)


class AppError(Exception):
    """Base class of all domain errors. Deliberately knows nothing about FastAPI.

    An unhandled exception becomes a 500 anyway -- this exists for every error
    that should NOT be one. A subclass sets the status and a stable `code` the
    dashboard can branch on; db/ and products/ raise it without importing
    FastAPI, and `register_exception_handlers` below turns it into the response.

    Raised directly, it is the generic 500 -- which is also how the unhandled
    case is documented.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    title: str = "Internal server error"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title


class UnauthorizedError(AppError):
    """No token, or not a valid one -- 401.

    401 means "authenticate (again)", 403 means "authenticated, but not for
    this". The dashboard branches on exactly that difference: 401 sends the
    user back through the Keycloak login, 403 shows "you lack the role".
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    title = "Authentication required"


class ForbiddenError(AppError):
    """The caller is known but is not allowed to see this data product."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    title = "Access denied"


class NotFoundError(AppError):
    """No such thing -- or none this caller may see.

    api/v1/catalog.py answers both cases the same way on purpose: otherwise the
    status code alone would tell an unauthorised caller which products exist.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    title = "Not found"


class InvalidRequestError(AppError):
    """The request parameters do not fit the contract -- 422.

    FastAPI raises this case itself (as `RequestValidationError`) and the
    handler below translates it. The class exists so the 422 is declared like
    every other error instead of being a special case in two places.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    title = "Invalid request"


class ConflictError(AppError):
    """The write contradicts data that already exists -- a duplicate key, a
    violated constraint -> 409.

    Unlike a 503, retrying will not help: the input has to change. db/sources.py
    raises it for Neo4j's ConstraintError and SQL's IntegrityError.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    title = "Conflict with existing data"


class UpstreamUnavailableError(AppError):
    """A data source (Neo4j/Postgres) is unreachable -> 503, not 500.

    The distinction matters to the dashboards: 503 means "try again later",
    500 means "this is a bug, please report it".
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "upstream_unavailable"
    title = "Upstream data source unavailable"


class ConfigurationError(AppError):
    """A required setting is missing -- our fault, so it stays a 500."""

    code = "configuration_error"
    title = "Server misconfigured"


class Problem(BaseModel):
    """The body of every error response -- and the model /docs shows.

    `_problem()` builds its responses from this model, so the documentation
    cannot describe a shape the API does not send.
    """

    type: str = "about:blank"
    title: str = Field(examples=["Upstream data source unavailable"])
    status: int = Field(examples=[503])
    detail: str = Field(examples=["Neo4j unavailable: no route to host"])
    code: str = Field(examples=["upstream_unavailable"])
    request_id: str | None = Field(default=None, examples=["3f2a9c1b4d5e6f70"])
    errors: list[dict[str, Any]] | None = Field(
        default=None,
        description="Field-level errors. Only present on a 422.",
        examples=[[{"type": "int_parsing", "loc": ["query", "limit"],
                    "msg": "Input should be a valid integer"}]],
    )


def documented_errors(*errors: type[AppError]) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` for the errors a route can answer.

        responses=documented_errors(ConflictError)

    Pass the classes, not status codes: status, description and example all come
    from the class, so documenting a new error type is one name on one route.

    Without this, /docs shows FastAPI's default 422 (`detail` as a LIST, which
    this API never sends) and no 401/403/409/500/503 at all -- a dashboard would
    be built against a shape that does not exist.
    """
    return {
        error.status_code: {
            "model": Problem,
            "description": error.title,
            "content": {"application/problem+json": {}},
        }
        for error in errors
    }


def _problem(request: Request, error: AppError, **extra: Any) -> JSONResponse:  # noqa: ANN401
    """Builds the one and only error response shape of this API.

    ANN401: `extra` carries RFC 9457 "extension members" -- additional top-level
    fields the format explicitly allows. Today that is the validation handler's
    `errors` list; the values have to be JSON-serialisable.
    """
    # Request object first, ContextVar second: on a 500 this handler runs
    # outside RequestContextMiddleware, whose `finally` has already reset the
    # ContextVar.
    request_id = getattr(request.state, "request_id", None) or request_id_var.get()
    body = Problem(
        title=error.title,
        status=error.status_code,
        detail=error.detail,
        code=error.code,
        request_id=request_id,
        **extra,
    )
    return JSONResponse(
        status_code=error.status_code,
        media_type="application/problem+json",
        # `exclude_none` keeps `errors` out of the responses that have none.
        content=body.model_dump(exclude_none=True),
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
        return _problem(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Raised by the framework, not by us: an unknown route, a wrong method.
        error = AppError(str(exc.detail))
        error.status_code, error.code, error.title = exc.status_code, "http_error", "HTTP error"
        return _problem(request, error)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            request,
            InvalidRequestError("The request parameters are invalid."),
            # The field-level errors -- helps when debugging Dash callbacks.
            # Round-tripped through `default=str` because `ctx` can carry
            # exception objects that json.dumps refuses: an error handler that
            # raises on its own is the worst possible failure mode here.
            errors=json.loads(json.dumps(exc.errors(), default=str)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("Unhandled error: %s", exc, exc_info=exc)
        return _problem(request, AppError("Unexpected error."))
