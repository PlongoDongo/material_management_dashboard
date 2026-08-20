"""
Gemeinsame Dependencies.

FastAPIs Dependency Injection ist der Mechanismus, ueber den alles
Request-Gebundene hereinkommt: Settings, Datenbank-Sessions, der Aufrufer.
Der Vorteil gegenueber globalen Objekten oder Imports ist die Testbarkeit --
`app.dependency_overrides[get_repositories] = fake` ersetzt in Tests die
komplette Datenschicht, ohne dass irgendetwas gepatcht werden muss.

Die langlebigen Objekte (Neo4j-Treiber, SQL-Engine) haengen an `app.state` und
werden in der Lifespan erzeugt/geschlossen -- nicht als Modul-Globals, sonst
teilen sich Tests und App dasselbe Objekt.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

from fastapi import Depends, Request

from data_api.core.config import Settings, get_settings
from data_api.db.repositories import Repositories

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_repositories(
    request: Request, settings: SettingsDep
) -> AsyncIterator[Repositories]:
    """Ein Repositories-Container pro Request.

    Der `AsyncExitStack` ist der Grund, warum niemand `session.close()` schreiben
    muss: alles, was der Container waehrend des Requests geoeffnet hat, wird beim
    Verlassen des `async with` geschlossen -- auch wenn der Endpunkt eine
    Exception wirft.
    """
    async with AsyncExitStack() as stack:
        yield Repositories(
            stack=stack,
            settings=settings,
            neo4j_driver=getattr(request.app.state, "neo4j_driver", None),
            sql_sessionmaker=getattr(request.app.state, "sql_sessionmaker", None),
        )


ReposDep = Annotated[Repositories, Depends(get_repositories)]
