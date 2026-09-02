"""
Request-id middleware.

Every request gets an id (or adopts the one in `X-Request-ID`, if a reverse
proxy already set one). The id ends up in every log line and in the response
header -- the bridge between "the table was empty in the dashboard" and the
matching line in the server log.
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from data_api.core.logging import request_id_var

log = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]

        # Stored twice on purpose. The ContextVar carries the id into log lines;
        # the request object carries it to the exception handlers, which run
        # OUTSIDE this middleware (Starlette's ServerErrorMiddleware sits
        # further out) and can no longer see the ContextVar.
        request.state.request_id = request_id
        token = request_id_var.set(request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
            # The log call sits INSIDE the try: if the ContextVar were reset
            # first, the one line that ties path, status and duration together
            # would carry "-" instead of the id.
            log.info("%s %s -> %s (%.1f ms)", request.method, request.url.path,
                     response.status_code, elapsed_ms)
            return response
        finally:
            request_id_var.reset(token)
