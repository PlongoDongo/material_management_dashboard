"""
The same two endpoints as relationships.py -- written with the ORM.

Variant B, side by side with variant A so the team can compare them on the real
case rather than in the abstract. Everything around the write is identical:
role check, cache declaration, one transaction per request, error translation,
the same request and response models.

    A  api/v1/relationships.py       await sources.postgres(INSERT_CHANGELOG, ...)
    B  api/v1/relationships_orm.py   await sources.add(Changelog(...))

What B does NOT have to spell out, because `Changelog` fills it in:
`changelog_id`, `sync_status` and `sync_attempts`. What B gives up: the SQL that
actually runs is no longer in the file -- and a table class with `table=True`
does not validate, so the class guards nothing on its own (db/models.py).

Both routers are mounted while the decision is open. One of them gets deleted
afterwards; they share the API models, so whichever stays keeps them.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status

from api.deps import SourcesDep
from api.v1.relationships import (
    INVALIDATES,
    SYSTEM_USER,
    UNKNOWN_SESSION,
    WRITE_ROLE,
    ChangelogEntry,
    MaterialRelationship,
)
from core.errors import ConflictError, documented_errors
from core.security import CurrentPrincipal, Principal, requires
from db.models import Changelog
from db.sources import Sources
from products.cache import invalidates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/material-relationships-orm",
                   tags=["Material relationships (ORM variant)"])


async def _record(
    sources: Sources,
    principal: Principal,
    change_type: str,
    relationship: MaterialRelationship,
) -> ChangelogEntry:
    """Appends one changelog entry and returns what was written."""
    entry = Changelog(
        user_id=principal.label if principal.auth_enabled else SYSTEM_USER,
        change_type=change_type,
        # `mode="json"` because the column is JSON and a UUID is not JSON.
        payload=relationship.model_dump(mode="json"),
        session_id=UNKNOWN_SESSION,
    )
    await sources.add(entry)

    log.info("Changelog %s: %s by %s", entry.changelog_id, change_type, principal.label)
    return ChangelogEntry(
        changelog_id=entry.changelog_id,
        change_type=entry.change_type,
        recorded_at=entry.created_at,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Record that two material representations are the same (ORM variant)",
    dependencies=[Depends(requires(WRITE_ROLE)), Depends(invalidates(*INVALIDATES))],
    responses=documented_errors(ConflictError),
)
async def create_relationship(
    relationship: Annotated[MaterialRelationship, Body()],
    sources: SourcesDep,
    principal: CurrentPrincipal,
) -> ChangelogEntry:
    return await _record(sources, principal, "MATERIALS_RELATIONSHIP_CREATED", relationship)


@router.delete(
    "",
    summary="Record that the relationship no longer holds (ORM variant)",
    dependencies=[Depends(requires(WRITE_ROLE)), Depends(invalidates(*INVALIDATES))],
    responses=documented_errors(ConflictError),
)
async def delete_relationship(
    relationship: Annotated[MaterialRelationship, Query()],
    sources: SourcesDep,
    principal: CurrentPrincipal,
) -> ChangelogEntry:
    return await _record(sources, principal, "MATERIALS_RELATIONSHIP_DELETED", relationship)
