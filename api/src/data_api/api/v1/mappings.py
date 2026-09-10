"""
The write side -- hand-written endpoints, deliberately NOT via the registry.

Why the separation? Because reading and writing have different contracts:

    Data product (GET)   a contract about the SHAPE of data. Cacheable,
                         idempotent, versioned, generatable.
    Command (POST/PATCH/DELETE)
                         a contract about an ACTION. It has preconditions, side
                         effects, permissions, transactions, and it invalidates
                         caches.

This is "CQRS-lite": a generic generator cannot produce the second kind, and
trying makes the abstraction worse. Write endpoints therefore live in ordinary,
hand-written routers under /api/v1/<topic>.

On HTTP methods -- there is no "UPDATE" in HTTP; people usually mean PATCH:
    POST    create, or trigger an action              not idempotent
    PUT     replace completely (the whole record)     idempotent
    PATCH   change partially (only the fields sent)   idempotent
    DELETE  remove                                    idempotent
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel, Field

from data_api.api.deps import SourcesDep
from data_api.core.security import CurrentPrincipal, requires
from data_api.products.cache import invalidates

log = logging.getLogger(__name__)

# The Keycloak role a caller needs in order to CHANGE anything here. Reading a
# data product and rewriting the master data are different privileges, so they
# get different checks -- adjust the name to match your realm.
WRITE_ROLE = "material-planner"

# Which read products go stale when something here changes. Declared at the
# route rather than called inside the handler: it cannot be forgotten (the
# architecture test insists on it), it runs only when the write actually
# succeeded, and architecture.py reads it to draw the edge in the diagram.
INVALIDATES = ("material-overview",)

router = APIRouter(prefix="/mappings", tags=["Mappings (write)"])


class MappingIn(BaseModel):
    """Input model. Always separate from the output model.

    The client must not set `id` or `changed_at`; in a shared model they would
    have to be validated away. Two small models are simpler than one large model
    with exceptions.
    """

    material_number: str = Field(min_length=1, max_length=40)
    target_material_group: str = Field(min_length=1, max_length=80)
    comment: str | None = Field(None, max_length=500)


class MappingOut(MappingIn):
    id: str
    changed_at: dt.datetime
    changed_by: str


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new mapping",
    dependencies=[Depends(requires(WRITE_ROLE)), Depends(invalidates(*INVALIDATES))],
    responses={
        403: {"description": f"The caller lacks the '{WRITE_ROLE}' role."},
        409: {"description": "The mapping already exists."},
    },
)
async def create_mapping(
    payload: Annotated[MappingIn, Body()],
    sources: SourcesDep,
    principal: CurrentPrincipal,
) -> MappingOut:
    """Creates a material-to-material-group mapping.

    The actual write belongs in a query here (not implemented yet, the target
    table does not exist). Note what is NOT in this function: the role check and
    the cache invalidation both sit in `dependencies` above, so neither can be
    forgotten and both are visible without reading the body.
    """
    # TODO(data source): await sources.postgres(INSERT_SQL, ...)
    # The commit happens automatically in the request scope (api/deps.py) -- but
    # only on the success path. Raise here and nothing is written.
    log.info("Mapping created by %s: %s -> %s",
             principal.label, payload.material_number, payload.target_material_group)

    return MappingOut(
        **payload.model_dump(),
        id=f"map-{payload.material_number}",
        changed_at=dt.datetime.now(dt.UTC),
        changed_by=principal.label,
    )


@router.patch(
    "/{mapping_id}",
    summary="Partially change a mapping",
    dependencies=[Depends(requires(WRITE_ROLE)), Depends(invalidates(*INVALIDATES))],
    responses={403: {"description": f"The caller lacks the '{WRITE_ROLE}' role."}},
)
async def patch_mapping(
    mapping_id: str,
    payload: Annotated[MappingIn, Body()],
    sources: SourcesDep,
    principal: CurrentPrincipal,
) -> MappingOut:
    # TODO(data source): same as create_mapping (see the commit note there).
    return MappingOut(
        **payload.model_dump(),
        id=mapping_id,
        changed_at=dt.datetime.now(dt.UTC),
        changed_by=principal.label,
    )
