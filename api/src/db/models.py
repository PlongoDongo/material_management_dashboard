"""
Table definitions for the ORM write path (SQLModel).

One class describes a table twice over: as a Pydantic model (fields, types) and
as a SQLAlchemy table. Routes that write through `sources.add(...)` hand over
objects of these classes instead of an INSERT.

    entry = Changelog(user_id="a.schmidt", change_type="...", payload={...})
    await sources.add(entry)

WHAT THE CLASS DOES AND DOES NOT DO
===================================
`default=` here is a PYTHON default: it fills the attribute when an object is
created through this class. It does NOT become a DDL default. The generated
table says so:

    created_at    TIMESTAMP DEFAULT now() NOT NULL   <- sa_column_kwargs, real DDL
    sync_status   VARCHAR(50) NOT NULL               <- default="pending" is NOT here
    sync_attempts INTEGER NOT NULL                   <- default=0 is NOT here

So `sources.add(...)` gets them from the class, while an INSERT written by hand
(`sources.postgres(...)`) would have to set those columns itself. Write through
the class and the question does not come up.

A second one: a class with `table=True` does NOT validate on instantiation --
`Changelog(sync_attempts="many")` is accepted here and rejected by the database.
Validation of what a CALLER sends therefore stays where it is: on the plain
Pydantic models in the router.

The `changelog` table belongs to another team; this file only describes it so
we can append to it.
"""
from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import JSON, DateTime, Field, SQLModel, text


class Changelog(SQLModel, table=True):
    """One recorded change, waiting for the sync process to apply it."""

    __tablename__ = "changelog"

    changelog_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(default="system", max_length=255)
    change_type: str = Field(max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    session_id: str = Field(default="unknown", max_length=255)
    created_at: dt.datetime = Field(
        sa_type=DateTime, sa_column_kwargs={"server_default": text("now()")}
    )
    sync_status: str = Field(default="pending", max_length=50)
    sync_error: str | None = Field(default=None)
    sync_attempts: int = Field(default=0)
    synced_at: dt.datetime | None = Field(default=None)
