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

from api.v1 import build_v1_router
from core.config import Settings, __version__, get_settings
from core.errors import (
    AppError,
    ConfigurationError,
    ForbiddenError,
    InvalidRequestError,
    UnauthorizedError,
    UpstreamUnavailableError,
    documented_errors,
    register_exception_handlers,
)
from core.logging import configure_logging
from core.middleware import RequestContextMiddleware
from db.neo4j import close_driver, create_driver
from db.sql import create_engine, create_sessionmaker, dispose_engine
from products.registry import discover, registry

log = logging.getLogger(__name__)

DESCRIPTION = """
Layer between the Dash dashboards and the data sources (Neo4j, Postgres, and
further services later on).

* **Data products** (`/api/v1/data-products/...`) -- versioned, read-only
  contracts with a fixed schema.
* **Catalog** (`/api/v1/catalog`) -- which products exist in which versions.
* **Commands** (e.g. `/api/v1/mappings`) -- write endpoints.

Versioning: the path carries the MAJOR (`/v3`), the full `MAJOR.MINOR` is in
`meta.version` of the response. New field = MINOR, same route. Field removed or
renamed = MAJOR, new route, the old one stays available until its `Sunset` date.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    # Auth is derived from OIDC_ISSUER being set (core/config.py). The cost of
    # that convenience is that a FORGOTTEN issuer leaves the API wide open, and
    # nothing would say so. In production that must be a failure to start, not
    # a quiet default -- a server that does not come up gets noticed within
    # minutes; an open one might not get noticed at all.
    #
    # ALLOW_ANONYMOUS is the way out for a deployment that genuinely does not
    # want authentication (a closed network). It does not weaken the check, it
    # only turns "forgot to configure it" into "wrote down that we do not want
    # it" -- and the warning below makes sure the state is visible in the log
    # of every single start.
    if settings.api_env == "prod" and not settings.auth_enabled:
        if not settings.allow_anonymous:
            raise ConfigurationError(
                "API_ENV=prod requires OIDC_ISSUER to be set. If this deployment is "
                "deliberately unauthenticated (closed network), set ALLOW_ANONYMOUS=true."
            )
        log.warning("Authentication is OFF and ALLOW_ANONYMOUS=true -- every caller "
                    "that reaches this API is treated as anonymous with full access.")

    # The assignments live INSIDE the try: if `create_engine` fails (a bad
    # DSN), the already-connected Neo4j driver would otherwise stay open --
    # under `--reload` those pile up.
    app.state.neo4j_driver = None
    app.state.sql_engine = None
    app.state.sql_sessionmaker = None
    try:
        app.state.neo4j_driver = await create_driver(
            settings.neo4j_uri,
            settings.neo4j_auth,
            max_connection_pool_size=settings.neo4j_max_connection_pool_size,
            connection_acquisition_timeout=settings.neo4j_connection_acquisition_timeout,
        )
        app.state.sql_engine = create_engine(settings.sql_url, ssl=settings.sql_ssl)
        app.state.sql_sessionmaker = create_sessionmaker(app.state.sql_engine)

        log.info("Ready: %d data products, env=%s", len(registry), settings.api_env)
        yield
    finally:
        await close_driver(app.state.neo4j_driver)
        await dispose_engine(app.state.sql_engine)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.server_loglevel)

    # Load the catalog BEFORE building the routes -- the registry must be
    # complete when the data product router is created.
    discover()

    app = FastAPI(
        title=settings.api_title,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        # Every route can answer with these, so they are declared once here
        # instead of on each route (core/errors.py). AppError is the generic 500.
        responses=documented_errors(UnauthorizedError, ForbiddenError, InvalidRequestError,
                                    AppError, UpstreamUnavailableError),
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
    if settings.cors_origins:
        # Needed because the Dash apps run on a different port than the API.
        # In production always list explicit origins -- never ["*"] with auth.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=settings.cors_allow_methods,
            allow_headers=settings.cors_allow_headers,
            expose_headers=["X-Request-ID", "X-Data-Product-Version", "ETag"],
        )

    register_exception_handlers(app)
    app.include_router(build_v1_router())
    return app
