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
        # Zweifach ablegen. Der ContextVar traegt die ID in die Logzeilen; das
        # Request-Objekt traegt sie zu den Fehler-Handlern, die AUSSERHALB
        # dieser Middleware laufen (Starlettes ServerErrorMiddleware sitzt
        # weiter aussen) und den ContextVar deshalb nicht mehr sehen.
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
            # Der Log-Aufruf steht INNERHALB des try: Wuerde der ContextVar
            # vorher zurueckgesetzt, traege ausgerechnet die eine Zeile, die
            # Pfad, Status und Dauer zusammenbringt, ein "-" statt der ID.
            log.info("%s %s -> %s (%.1f ms)", request.method, request.url.path,
                     response.status_code, elapsed_ms)
            return response
        finally:
            request_id_var.reset(token)
