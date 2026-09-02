"""
Logging setup: one call at application start, then `logging.getLogger(__name__)`
everywhere else.

Every log line carries the request id (see core/middleware.py). That is what
turns a dashboard complaint ("the table was empty") into a specific line in the
server log, via the response headers.
"""
from __future__ import annotations

import contextvars
import logging
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # The neo4j driver is very chatty at DEBUG level.
    logging.getLogger("neo4j").setLevel("WARNING")
