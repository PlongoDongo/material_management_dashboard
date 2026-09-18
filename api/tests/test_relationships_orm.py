"""
The ORM variant of the relationship routes (api/v1/relationships_orm.py).

Same expectations as tests/test_relationships.py -- the difference is what a
test can look at: variant A hands SQL and parameters to the database, so the
tests check those; variant B hands over an object, so the tests check the
object.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from tests.fakes import FakeSources
from tests.types import AuthHeader

from api.deps import get_sources
from app import create_app
from core.config import Settings
from db.models import Changelog

PATH = "/api/v1/material-relationships-orm"
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
    [entry] = fake_sources.added
    assert isinstance(entry, Changelog)
    assert entry.change_type == "MATERIALS_RELATIONSHIP_CREATED"
    assert entry.payload == RELATIONSHIP
    assert response.json()["changelog_id"] == str(entry.changelog_id)


def test_the_model_fills_in_what_the_route_does_not_say(
    client: TestClient, fake_sources: FakeSources
) -> None:
    """THE difference to variant A.

    `changelog_id`, `sync_status` and `sync_attempts` are Python defaults on the
    table class, so the route never mentions them. The hand-written INSERT has
    to set all three, because those defaults never reach the DDL.
    """
    client.post(PATH, json=RELATIONSHIP)

    [entry] = fake_sources.added
    assert entry.changelog_id is not None
    assert entry.sync_status == "pending"
    assert entry.sync_attempts == 0
    assert entry.sync_error is None and entry.synced_at is None


def test_a_removed_relationship_is_recorded_rather_than_deleted(
    client: TestClient, fake_sources: FakeSources
) -> None:
    """DELETE writes the intent -- the sync takes the relationship apart."""
    response = client.delete(PATH, params=RELATIONSHIP)

    assert response.status_code == 200
    [entry] = fake_sources.added
    assert entry.change_type == "MATERIALS_RELATIONSHIP_DELETED"
    assert response.json()["change_type"] == "MATERIALS_RELATIONSHIP_DELETED"


def test_without_authentication_the_entry_belongs_to_the_system(
    client: TestClient, fake_sources: FakeSources
) -> None:
    client.post(PATH, json=RELATIONSHIP)

    [entry] = fake_sources.added
    assert entry.user_id == "system"
    assert entry.session_id == "unknown"


def test_the_entry_names_the_authenticated_user(
    oidc_settings: Settings, auth_header: AuthHeader
) -> None:
    client, fake = _secured(oidc_settings)
    with client:
        response = client.post(PATH, json=RELATIONSHIP,
                               headers=auth_header(username="a.schmidt",
                                                   roles=["material-planner"]))

    assert response.status_code == 201
    assert fake.added[0].user_id == "a.schmidt"


def test_without_the_role_nothing_is_written(
    oidc_settings: Settings, auth_header: AuthHeader
) -> None:
    """The guard has to run BEFORE the write, not after it."""
    client, fake = _secured(oidc_settings)
    with client:
        response = client.post(PATH, json=RELATIONSHIP, headers=auth_header(roles=["viewer"]))

    assert response.status_code == 403
    assert fake.added == []


def test_an_unknown_relationship_type_is_rejected(client: TestClient) -> None:
    """The request models are shared with variant A, so this holds for both:
    the table class itself validates nothing (`table=True`)."""
    response = client.post(PATH, json={**RELATIONSHIP, "relationship_type": "IS_SIMILAR"})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_the_table_class_does_not_validate() -> None:
    """Why the request model in the router is not redundant.

    A SQLModel class with `table=True` skips validation: this assignment is
    accepted here and only refused by the database.
    """
    entry = Changelog(change_type="X", sync_attempts="many")

    assert entry.sync_attempts == "many"
