"""
Entry point.

    Development:  uvicorn data_api.main:app --reload --port 8000
    Production :  uvicorn data_api.main:app --host 0.0.0.0 --port 8000 --workers 4

On --workers: each worker is a separate process with its own Neo4j driver, its
own SQL pool and its own in-process cache. Keep that in mind when sizing the
database pools (pool_size * workers), and it is the reason the cache should move
to Redis eventually.
"""
from __future__ import annotations

from data_api.application import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("data_api.main:app", host="127.0.0.1", port=8000, reload=True)
