"""
Request-ID-Middleware.

Jeder Request bekommt eine ID (oder uebernimmt die aus `X-Request-ID`, falls ein
Reverse Proxy schon eine gesetzt hat). Die ID landet in jeder Logzeile und im
Response-Header -- das ist die Bruecke zwischen "im Dashboard war die Tabelle
leer" und der passenden Zeile im Serverlog.
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
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
        log.info("%s %s -> %s (%.1f ms)", request.method, request.url.path,
                 response.status_code, elapsed_ms)
        return response
