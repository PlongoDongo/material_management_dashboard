"""
Caching of data products.

Why at all? Dashboards ask for the same data constantly (every callback, every
user, every reload). A Cypher aggregation that takes 800 ms must not run 40
times a minute.

Cache key = (product name, major, parameters). Different filters are different
answers -- forgetting that is the classic cache bug.

Limitation of this implementation: the cache lives IN THE PROCESS. With several
uvicorn workers each worker has its own. Fine to start with (the data is only
seconds-fresh anyway). Once it matters, swap `TTLCache` for Redis -- the
interface (get/set) stays the same.
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
            # Simplest eviction: earliest expiry first.
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.monotonic() + ttl, value)

    def invalidate(self, product: str | None = None) -> int:
        """Clear selectively after a write (see api/v1/mappings.py)."""
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
    """Weak ETag over the serialised payload.

    Benefit: a polling client can send `If-None-Match` and get a 304 with no
    body when nothing changed. Saves bandwidth and re-rendering of large tables.
    """
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return 'W/"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
