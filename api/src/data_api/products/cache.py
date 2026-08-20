"""
Caching der Datenprodukte.

Warum ueberhaupt? Dashboards fragen dieselben Daten oft an (jeder Callback,
jeder Nutzer, jeder Reload). Eine Cypher-Aggregation, die 800 ms braucht, darf
nicht 40x pro Minute laufen.

Cache-Schluessel = (Produktname, Major, Parameter). Verschiedene Filter sind
verschiedene Antworten -- das ist die haeufigste Cache-Bug-Quelle.

Grenze dieser Implementierung: der Cache liegt IM PROZESS. Mit mehreren
uvicorn-Workern hat jeder Worker seinen eigenen. Fuer den Anfang voellig okay
(die Daten sind ohnehin nur sekundenaktuell). Sobald das stoert, tauscht man
`TTLCache` gegen Redis -- die Schnittstelle (get/set) bleibt gleich.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class TTLCache:
    def __init__(self, max_entries: int = 512) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._max = max_entries

    @staticmethod
    def make_key(product: str, major: int, params: str) -> str:
        digest = hashlib.sha256(params.encode()).hexdigest()[:16]
        return f"{product}:v{major}:{digest}"

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        if ttl <= 0:
            return
        if len(self._store) >= self._max:
            # Simpelste Verdraengung: aeltestes Ablaufdatum zuerst.
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.monotonic() + ttl, value)

    def invalidate(self, product: str | None = None) -> int:
        """Nach einem Schreibvorgang gezielt leeren (siehe api/v1/mappings.py)."""
        if product is None:
            count = len(self._store)
            self._store.clear()
            return count
        doomed = [k for k in self._store if k.startswith(f"{product}:")]
        for key in doomed:
            self._store.pop(key, None)
        return len(doomed)


cache = TTLCache()


def etag_for(payload: Any) -> str:
    """Schwaches ETag ueber den serialisierten Payload.

    Nutzen: das Dashboard schickt beim Polling `If-None-Match` und bekommt 304
    ohne Body zurueck, wenn sich nichts geaendert hat. Spart Bandbreite und
    das erneute Rendern grosser Tabellen.
    """
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return 'W/"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
