"""
Relationships between material representations -- the write side.

Two endpoints that record ONE thing: "these two material representations are
the same" (or no longer are). They do not change the relationship themselves;
they append a row to the `changelog` table:

    POST   /api/v1/material-relationships   ->  MATERIALS_RELATIONSHIP_CREATED
    DELETE /api/v1/material-relationships   ->  MATERIALS_RELATIONSHIP_DELETED

`changelog` is an OUTBOX, not a log for humans: its `sync_status`,
`sync_attempts` and `synced_at` columns say that a separate process picks the
entries up and applies them to the source system. That is why the DELETE route
deletes nothing here -- it records the intent, and the sync carries it out.

The row is written through the table class in db/models.py: the route names what
it knows, and the class fills in the rest -- the id, the sync columns, and
"system" / "unknown" when nobody is signed in.
"""
from __future__ import annotations

import datetime as dt
import logging
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel

from api.deps import SourcesDep
from core.errors import ConflictError, documented_errors
from core.security import CurrentPrincipal, Principal, requires
from db.models import Changelog
from db.sources import Sources
from products.cache import invalidates

log = logging.getLogger(__name__)

# The Keycloak role a caller needs to change master data.
WRITE_ROLE = "material-planner"

# No data product goes stale: the overview is fed from Neo4j and only changes
# once the sync has applied the changelog entry. Declared explicitly, because a
# missing `invalidates(...)` is an oversight and an empty one is a decision.
INVALIDATES: tuple[str, ...] = ()

router = APIRouter(prefix="/material-relationships", tags=["Material relationships (write)"])


class RelationshipType(StrEnum):
    IS_SAME = "IS_SAME"


class MaterialRelationship(BaseModel):
    """Which two material representations are related, and how.

    A plain Pydantic model on purpose: it checks what a CALLER sends. The table
    class cannot -- with `table=True` SQLModel does not validate (db/models.py).
    """

    material_rep_1_id: UUID
    material_rep_2_id: UUID
    relationship_type: RelationshipType = RelationshipType.IS_SAME


class ChangelogEntry(BaseModel):
    """What the API confirms: the row it appended, not the applied change.

    `changelog_id` is what to quote when asking whether the sync picked an
    entry up.
    """

    changelog_id: UUID
    change_type: str
    recorded_at: dt.datetime


async def _record(
    sources: Sources,
    principal: Principal,
    change_type: str,
    relationship: MaterialRelationship,
) -> ChangelogEntry:
    """Appends one changelog entry and returns what was written."""
    entry = Changelog(
        change_type=change_type,
        # `mode="json"`: the column is JSON, and a UUID is not.
        payload=relationship.model_dump(mode="json"),
    )
    if principal.auth_enabled:
        # The readable name, not the raw Keycloak `sub`. Without sign-in the
        # class default applies: "system".
        entry.user_id = principal.label
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
    summary="Record that two material representations are the same",
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
    summary="Record that the relationship no longer holds",
    dependencies=[Depends(requires(WRITE_ROLE)), Depends(invalidates(*INVALIDATES))],
    responses=documented_errors(ConflictError),
)
async def delete_relationship(
    relationship: Annotated[MaterialRelationship, Query()],
    sources: SourcesDep,
    principal: CurrentPrincipal,
) -> ChangelogEntry:
    """The relationship is taken apart by the sync, not here -- see the module
    docstring. The parameters go in the query string, so this route has no body."""
    return await _record(sources, principal, "MATERIALS_RELATIONSHIP_DELETED", relationship)
