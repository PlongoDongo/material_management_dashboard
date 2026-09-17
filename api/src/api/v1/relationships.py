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

The table is owned elsewhere; this API only appends to it (see INSERT_CHANGELOG).
"""
from __future__ import annotations

import datetime as dt
import logging
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel

from api.deps import SourcesDep
from core.errors import ConflictError, documented_errors
from core.security import CurrentPrincipal, Principal, requires
from db.sources import Sources
from products.cache import invalidates

log = logging.getLogger(__name__)

# The Keycloak role a caller needs to change master data -- the same one the
# other write routes use.
WRITE_ROLE = "material-planner"

# No data product goes stale: the overview is fed from Neo4j and only changes
# once the sync has applied the changelog entry. Declared explicitly, because a
# missing `invalidates(...)` is an oversight and an empty one is a decision.
INVALIDATES: tuple[str, ...] = ()

# Written when nobody is authenticated -- which is the normal case while the API
# runs without OIDC. The columns are UUIDs, so "system" cannot be spelled out.
SYSTEM_ID = UUID("00000000-0000-0000-0000-000000000000")

# Appends one entry. `payload` is handed over as JSON text: that is what the
# driver expects for a json/jsonb column, and it keeps the shape of the entry
# in one place -- the model below.
INSERT_CHANGELOG = """
INSERT INTO changelog (changelog_id, user_id, change_type, payload, session_id)
VALUES (:changelog_id, :user_id, :change_type, :payload, :session_id)
RETURNING changelog_id, created_at
"""

router = APIRouter(prefix="/material-relationships", tags=["Material relationships (write)"])


class RelationshipType(StrEnum):
    IS_SAME = "IS_SAME"


class MaterialRelationship(BaseModel):
    """Which two material representations are related, and how."""

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
    [row] = await sources.postgres(
        INSERT_CHANGELOG,
        changelog_id=uuid4(),
        user_id=_user_id(principal),
        change_type=change_type,
        payload=relationship.model_dump_json(),
        session_id=SYSTEM_ID,
    )
    log.info("Changelog %s: %s by %s", row["changelog_id"], change_type, principal.label)
    return ChangelogEntry(
        changelog_id=row["changelog_id"],
        change_type=change_type,
        recorded_at=row["created_at"],
    )


def _user_id(principal: Principal) -> UUID:
    """The changelog stores a UUID. Keycloak's `sub` is one; with authentication
    switched off there is nobody to name, so the entry belongs to the system."""
    try:
        return UUID(principal.subject)
    except ValueError:
        return SYSTEM_ID


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
