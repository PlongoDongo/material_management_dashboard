"""
The write side for material relationships.

These routes change no relationship: they append one row to the `changelog`
outbox, which a separate process applies later. So the tests check what is
WRITTEN -- change type, payload, who -- and not any state in a graph.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from tests.fakes import FakeSources
from tests.types import AuthHeader

from api.deps import get_sources
from api.v1.relationships import INSERT_CHANGELOG, SYSTEM_USER
from app import create_app
from core.config import Settings

PATH = "/api/v1/material-relationships"
RELATIONSHIP = {
    "material_rep_1_id": "11111111-1111-1111-1111-111111111111",
    "material_rep_2_id": "22222222-2222-2222-2222-222222222222",
    "relationship_type": "IS_SAME",
}


def _secured(settings: Settings) -> tuple[TestClient, FakeSources]:
    """An app with authentication ON, but without a database."""
    application = create_app(settings)
    fake = FakeSources()
    application.dependency_overrides[get_sources] = lambda: fake
    return TestClient(application), fake


def test_a_new_relationship_is_appended_to_the_changelog(
    client: TestClient, fake_sources: FakeSources
) -> None:
    response = client.post(PATH, json=RELATIONSHIP)

    assert response.status_code == 201
    sql, parameters = fake_sources.calls[0]
    assert sql is INSERT_CHANGELOG
    assert parameters["change_type"] == "MATERIALS_RELATIONSHIP_CREATED"
    assert json.loads(parameters["payload"]) == RELATIONSHIP
    assert response.json()["changelog_id"] == str(parameters["changelog_id"])
    # Set explicitly: the table's `default="pending"` is a Python default on the
    # model class and does not apply to an INSERT that goes to the table.
    assert parameters["sync_status"] == "pending"


def test_a_removed_relationship_is_recorded_rather_than_deleted(
    client: TestClient, fake_sources: FakeSources
) -> None:
    """DELETE writes the intent -- the sync takes the relationship apart."""
    response = client.delete(PATH, params=RELATIONSHIP)

    assert response.status_code == 200
    sql, parameters = fake_sources.calls[0]
    assert sql is INSERT_CHANGELOG, "the route must not delete anything itself"
    assert parameters["change_type"] == "MATERIALS_RELATIONSHIP_DELETED"
    assert response.json()["change_type"] == "MATERIALS_RELATIONSHIP_DELETED"


def test_without_authentication_the_entry_belongs_to_the_system(
    client: TestClient, fake_sources: FakeSources
) -> None:
    """With auth off there is no user to name, so the entry looks like any other
    unattributed one -- the default the column declares."""
    client.post(PATH, json=RELATIONSHIP)

    _sql, parameters = fake_sources.calls[0]
    assert parameters["user_id"] == SYSTEM_USER


def test_the_entry_names_the_authenticated_user(
    oidc_settings: Settings, auth_header: AuthHeader
) -> None:
    """The readable name, not the raw `sub` UUID: somebody has to recognise the
    entry in the changelog later."""
    client, fake = _secured(oidc_settings)
    with client:
        response = client.post(PATH, json=RELATIONSHIP,
                               headers=auth_header(username="a.schmidt",
                                                   roles=["material-planner"]))

    assert response.status_code == 201
    assert fake.calls[0][1]["user_id"] == "a.schmidt"


def test_without_the_role_nothing_is_written(
    oidc_settings: Settings, auth_header: AuthHeader
) -> None:
    """The guard has to run BEFORE the write, not after it."""
    client, fake = _secured(oidc_settings)
    with client:
        response = client.post(PATH, json=RELATIONSHIP, headers=auth_header(roles=["viewer"]))

    assert response.status_code == 403
    assert fake.calls == []


def test_an_unknown_relationship_type_is_rejected(client: TestClient) -> None:
    """The enum is the contract: only IS_SAME exists today, and a typo must not
    end up in the outbox as a change type nobody can apply."""
    response = client.post(PATH, json={**RELATIONSHIP, "relationship_type": "IS_SIMILAR"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "validation_error"
