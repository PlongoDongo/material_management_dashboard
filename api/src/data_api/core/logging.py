"""
Logging-Setup: ein Aufruf beim App-Start, danach ueberall `logging.getLogger(__name__)`.

Jede Logzeile traegt die Request-ID mit (siehe core/middleware.py). Damit laesst
sich eine Dashboard-Beschwerde ("die Tabelle war leer") ueber die Response-Header
bis in die Serverlogs zurueckverfolgen.
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
    # Der neo4j-Treiber ist auf DEBUG sehr geschwaetzig.
    logging.getLogger("neo4j").setLevel("WARNING")
