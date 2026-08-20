"""
App-Fabrik und Lebenszyklus.

Zwei Dinge, die man in FastAPI-Projekten oft falsch sieht:

1. `app = FastAPI()` auf Modulebene, danach Router per Import-Nebenwirkung
   registrieren. Funktioniert, bis Tests zwei Apps mit unterschiedlicher
   Konfiguration brauchen. Darum eine `create_app()`-FABRIK: jeder Aufruf
   liefert eine frische, unabhaengige App.

2. Verbindungen beim Import oeffnen. Dann verbindet sich jeder `import`
   (auch der von pytest-Collection oder von einem Alembic-Skript) zur
   Datenbank. Darum LIFESPAN: FastAPI ruft den Hochlaufteil vor dem ersten
   Request und den Abbauteil beim Herunterfahren auf.

Merksatz zum Lebenszyklus:
    Lifespan  -> alles, was den ganzen Prozess lang lebt (Treiber, Engine, Pools)
    Depends   -> alles, was einen Request lang lebt (Sessions, Aufrufer)
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
Zwischenschicht zwischen den Dash-Dashboards und den Datenquellen
(Neo4j, Postgres, spaeter weitere Services).

* **Datenprodukte** (`/api/v1/data-products/...`) -- versionierte, lesende
  Vertraege. Jedes Produkt hat einen Owner und ein festes Schema.
* **Katalog** (`/api/v1/catalog`) -- welche Produkte gibt es in welchen Versionen.
* **Kommandos** (z. B. `/api/v1/mappings`) -- schreibende Endpunkte.

Versionierung: im Pfad steht das MAJOR (`/v2`), das volle `MAJOR.MINOR` steht in
`meta.version` der Antwort. Neues Feld = MINOR, gleiche Route. Feld entfernt oder
umbenannt = MAJOR, neue Route, alte bleibt bis zum `Sunset`-Datum erreichbar.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    app.state.neo4j_driver = await create_driver(settings.neo4j_uri, settings.neo4j_auth)
    app.state.sql_engine = create_engine(settings.postgres_dsn)
    app.state.sql_sessionmaker = create_sessionmaker(app.state.sql_engine)

    log.info("Bereit: %d Datenprodukte, env=%s", len(registry), settings.api_env)
    try:
        yield
    finally:
        await close_driver(app.state.neo4j_driver)
        await dispose_engine(app.state.sql_engine)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.api_log_level)

    # Katalog laden BEVOR die Routen gebaut werden -- die Registry muss beim
    # Erzeugen des Datenprodukt-Routers vollstaendig sein.
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
    # Die an create_app() uebergebenen Settings muessen gewinnen -- auch fuer
    # `Depends(get_settings)` tief in den Routern. Ohne dieses Override liest
    # jede Dependency wieder aus der Umgebung (get_settings ist lru_cache'd),
    # und ein Test oder eine zweite App mit anderer Konfiguration haette keine
    # Wirkung. `dependency_overrides` ist dafuer der vorgesehene Mechanismus.
    app.dependency_overrides[get_settings] = lambda: settings

    app.add_middleware(RequestContextMiddleware)
    if settings.api_cors_origins:
        # Noetig, weil die Dash-Apps auf einem anderen Port laufen als die API.
        # In prod immer explizite Origins -- niemals ["*"] zusammen mit Auth.
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
