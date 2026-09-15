"""
Shared dependencies.

A "dependency" is something FastAPI provides to the endpoint before calling it
-- here the settings and the access to the data sources.

The advantage over global objects: in tests a dependency can be replaced with a
single line (`app.dependency_overrides[get_sources] = ...`), without patching
anything.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

from fastapi import Depends, Request

from core.config import Settings, get_settings
from db.sources import Sources

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_sources(request: Request, settings: SettingsDep) -> AsyncIterator[Sources]:
    """One Sources object per request.

    Everything before `yield` runs before the endpoint, everything after it runs
    once the endpoint has returned -- but before the response is sent (see
    `SourcesDep`). The AsyncExitStack closes every connection opened during the
    request -- even if the endpoint raised.
    """
    async with AsyncExitStack() as stack:
        sources = Sources(
            stack=stack,
            settings=settings,
            neo4j_driver=getattr(request.app.state, "neo4j_driver", None),
            sql_sessionmaker=getattr(request.app.state, "sql_sessionmaker", None),
        )
        yield sources
        # Only reached if the endpoint completed without raising. If it raised,
        # control never gets here and the AsyncExitStack rolls back -- exactly
        # the split we want between success and failure.
        await sources.commit()


# scope="function": the code after `yield` -- the commit -- runs BEFORE the
# response is sent. With the default ("request") it runs afterwards, and a
# commit that fails is only logged while the client already holds its 201.
SourcesDep = Annotated[Sources, Depends(get_sources, scope="function")]
