"""
Application factory and lifecycle.

Two things often done wrong in FastAPI projects:

1. `app = FastAPI()` at module level, with routers registered as an import side
   effect. That works until tests need two apps with different configuration.
   Hence a `create_app()` FACTORY: every call returns a fresh, independent app.

2. Opening connections at import time. Then every `import` -- including the one
   pytest does while collecting tests, or an Alembic script -- connects to the
   database. Hence LIFESPAN: FastAPI runs the startup part before the first
   request and the shutdown part when the process stops.

Rule of thumb for the lifecycle:
    Lifespan -> everything that lives as long as the process (drivers, engines, pools)
    Depends  -> everything that lives for one request (sessions, the caller)
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data_api import __version__
from data_api.api.v1 import build_v1_router
from data_api.core.config import Settings, get_settings
from data_api.core.errors import register_exception_handlers
from data_api.core.logging import configure_logging
from data_api.core.middleware import RequestContextMiddleware
from data_api.db.neo4j import close_driver, create_driver
from data_api.db.sql import create_engine, create_sessionmaker, dispose_engine
from data_api.products.registry import discover, registry

log = logging.getLogger(__name__)

DESCRIPTION = """
Layer between the Dash dashboards and the data sources (Neo4j, Postgres, and
further services later on).

* **Data products** (`/api/v1/data-products/...`) -- versioned, read-only
  contracts. Every product has an owner and a fixed schema.
* **Catalog** (`/api/v1/catalog`) -- which products exist in which versions.
* **Commands** (e.g. `/api/v1/mappings`) -- write endpoints.

Versioning: the path carries the MAJOR (`/v3`), the full `MAJOR.MINOR` is in
`meta.version` of the response. New field = MINOR, same route. Field removed or
renamed = MAJOR, new route, the old one stays available until its `Sunset` date.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    # The assignments live INSIDE the try: if `create_engine` fails (a bad
    # DSN), the already-connected Neo4j driver would otherwise stay open --
    # under `--reload` those pile up.
    app.state.neo4j_driver = None
    app.state.sql_engine = None
    app.state.sql_sessionmaker = None
    try:
        app.state.neo4j_driver = await create_driver(settings.neo4j_uri, settings.neo4j_auth)
        app.state.sql_engine = create_engine(settings.postgres_dsn)
        app.state.sql_sessionmaker = create_sessionmaker(app.state.sql_engine)

        log.info("Ready: %d data products, env=%s", len(registry), settings.api_env)
        yield
    finally:
        await close_driver(app.state.neo4j_driver)
        await dispose_engine(app.state.sql_engine)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.api_log_level)

    # Load the catalog BEFORE building the routes -- the registry must be
    # complete when the data product router is created.
    discover()

    app = FastAPI(
        title=settings.api_title,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    # The settings passed to create_app() must win -- including for
    # `Depends(get_settings)` deep inside the routers. Without this override
    # every dependency would read from the environment again (get_settings is
    # lru_cache'd), and a test or a second app with different configuration
    # would have no effect. `dependency_overrides` is the intended mechanism.
    app.dependency_overrides[get_settings] = lambda: settings

    app.add_middleware(RequestContextMiddleware)
    if settings.api_cors_origins:
        # Needed because the Dash apps run on a different port than the API.
        # In production always list explicit origins -- never ["*"] with auth.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.api_cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "X-Data-Product-Version", "ETag"],
        )

    register_exception_handlers(app)
    app.include_router(build_v1_router())
    return app
