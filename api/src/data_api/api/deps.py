"""
Gemeinsame Dependencies.

Eine "Dependency" ist etwas, das FastAPI dem Endpunkt vor dem Aufruf
bereitstellt -- hier die Einstellungen und der Zugang zu den Datenquellen.

Der Vorteil gegenueber globalen Objekten: In Tests laesst sich eine Dependency
mit einer Zeile ersetzen (`app.dependency_overrides[get_sources] = ...`), ohne
dass irgendetwas gepatcht werden muss.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

from fastapi import Depends, Request

from data_api.core.config import Settings, get_settings
from data_api.db.sources import Sources

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_sources(request: Request, settings: SettingsDep) -> AsyncIterator[Sources]:
    """Ein Sources-Objekt pro Request.

    Alles vor `yield` laeuft vor dem Endpunkt, alles danach nach der Antwort.
    Der `AsyncExitStack` schliesst jede Verbindung, die waehrend des Requests
    geoeffnet wurde -- auch wenn der Endpunkt einen Fehler geworfen hat.
    """
    async with AsyncExitStack() as stack:
        yield Sources(
            stack=stack,
            settings=settings,
            neo4j_driver=getattr(request.app.state, "neo4j_driver", None),
            sql_sessionmaker=getattr(request.app.state, "sql_sessionmaker", None),
        )


SourcesDep = Annotated[Sources, Depends(get_sources)]
