"""
Entry point.

    Development:  python -m data_api.main
                  (or: uvicorn data_api.main:app --reload)
    Production :  uvicorn data_api.main:app --host 0.0.0.0 --port 8000 --workers 4

`python -m data_api.main` takes host, port and log level from Settings, so the
same SERVER_HOST / SERVER_PORT / SERVER_LOGLEVEL that configure a deployment
also configure a local run. Reload is on only for api_env="dev".

On --workers: each worker is a separate process with its own Neo4j driver, its
own SQL pool and its own in-process cache. Keep that in mind when sizing the
database pools (pool_size * workers), and it is the reason the cache should move
to Redis eventually.
"""
from __future__ import annotations

from data_api.app import create_app

app = create_app()


if __name__ == "__main__":
    # Imported here rather than at the top: uvicorn is only needed for this
    # entry point, so importing the module (pytest does) must not require it.
    import uvicorn

    from data_api.core.config import get_settings

    settings = get_settings()
    uvicorn.run(
        # The import STRING, not the `app` object above: reload needs to
        # re-import the module, and it cannot do that with an object it was
        # handed. Same for --workers.
        "data_api.main:app",
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.server_loglevel,
        reload=settings.api_env == "dev",
    )
