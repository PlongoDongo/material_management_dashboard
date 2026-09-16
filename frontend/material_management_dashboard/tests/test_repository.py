"""
Tests of the API connection -- without a running API server.

`httpx.MockTransport` intercepts the requests and answers with invented but
FORMAT-IDENTICAL responses. That way the complete chain runs:

    get_materials() -> DataProductClient.fetch() -> HTTP layer (mocked)
                    -> unwrap envelope -> _rows_to_frame() -> DataFrame

This is the same idea as in the API project (there: `dependency_overrides`):
only the outermost layer is replaced, everything above it runs for real.
"""
from __future__ import annotations

import httpx
import polars as pl
import pytest

from data import repository as repo
from data.api_client import DataProductClient, DataProductError, NotAuthorisedError
from data.schema import COLUMNS

# Rows exactly as the data product material-overview/v2 delivers them.
API_ROWS = [
    {"material_number": "MAT-1", "description": "Schraube", "material_group": "Rohstoffe",
     "plant_id": "W-KOE", "plant_name": "Werk Köln", "status": "Aktiv",
     "stock": 10, "price": 2.5, "stock_value": 25.0, "changed_on": "2026-01-01"},
    {"material_number": "MAT-2", "description": "Mutter", "material_group": None,
     "plant_id": "W-BER", "plant_name": "Werk Berlin", "status": "Gesperrt",
     "stock": None, "price": 1.0, "stock_value": None, "changed_on": "2026-02-01"},
]


def _envelope(rows: list[dict], **meta_over) -> dict:
    meta = {"product": "material-overview", "version": "3.0", "api_version": "v1",
            "generated_at": "2026-08-20T07:09:05Z", "row_count": len(rows),
            "total_count": len(rows), "source": "neo4j", "cache": "miss",
            "deprecated": False, "sunset": None}
    meta.update(meta_over)
    return {"meta": meta, "data": rows}


def _client(handler) -> DataProductClient:
    return DataProductClient(base_url="http://api.test",
                             transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _empty_cache():
    """Every test starts without a cache -- otherwise they bleed into each other."""
    repo._CACHE.clear()
    yield
    repo._CACHE.clear()


# --- Transformation (pure, without HTTP) -----------------------------------

def test_plant_name_becomes_the_plant_column() -> None:
    """The translation at the boundary API (English) <-> table (German)."""
    frame = repo._rows_to_frame(API_ROWS)
    assert frame["plant"].to_list() == ["Werk Köln", "Werk Berlin"]
    assert "plant_name" not in frame.columns


def test_the_frame_has_exactly_the_table_columns() -> None:
    assert repo._rows_to_frame(API_ROWS).columns == COLUMNS


def test_unknown_api_fields_are_ignored() -> None:
    """A new field in the API must NEVER break the dashboard.

    That is exactly why an added field is only a minor version.
    """
    rows = [dict(API_ROWS[0], brand_new_field="whatever")]
    assert repo._rows_to_frame(rows).columns == COLUMNS


def test_a_missing_field_becomes_none_instead_of_crashing(caplog) -> None:
    rows = [{k: v for k, v in API_ROWS[0].items() if k != "stock_value"}]
    frame = repo._rows_to_frame(rows)
    assert frame["stock_value"].to_list() == [None]
    assert "stock_value" in caplog.text


def test_an_empty_result_yields_a_schema_correct_frame() -> None:
    """Without a schema the table would crash on the first empty result."""
    frame = repo._rows_to_frame([])
    assert frame.height == 0
    assert frame.columns == COLUMNS


def test_stock_stays_none_and_does_not_become_zero() -> None:
    """None means 'unknown', not 'no stock'."""
    frame = repo._rows_to_frame(API_ROWS)
    assert frame["stock"].to_list() == [10, None]


# --- Client over HTTP (mocked) ---------------------------------------------

def test_the_client_calls_the_right_route() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_envelope(API_ROWS))

    rows, meta = _client(handler).fetch("material-overview", "v3", limit=50_000)
    assert seen["url"] == "http://api.test/api/v1/data-products/material-overview/v3?limit=50000"
    assert meta["version"] == "3.0"
    assert len(rows) == 2


def test_list_parameters_are_appended_repeatedly() -> None:
    """?status=Aktiv&status=Gesperrt -- exactly what FastAPI expects."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = str(request.url.query, "utf-8")
        return httpx.Response(200, json=_envelope([]))

    _client(handler).fetch("material-overview", "v3", status=["Aktiv", "Gesperrt"])
    assert seen["query"] == "status=Aktiv&status=Gesperrt"


def test_an_error_response_becomes_a_dataproducterror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"title": "Upstream data source unavailable",
                                         "detail": "Neo4j nicht erreichbar",
                                         "code": "upstream_unavailable"})

    with pytest.raises(DataProductError, match="Neo4j nicht erreichbar"):
        _client(handler).fetch("material-overview", "v3")


def test_an_unreachable_api_becomes_a_dataproducterror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(DataProductError, match="unreachable"):
        _client(handler).fetch("material-overview", "v3")


# --- get_materials: cache and failure behaviour ----------------------------

def test_get_materials_returns_a_dataframe(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_client",
                        _client(lambda r: httpx.Response(200, json=_envelope(API_ROWS))))
    frame = repo.get_materials()
    assert isinstance(frame, pl.DataFrame)
    assert frame.height == 2
    assert frame["plant"].to_list() == ["Werk Köln", "Werk Berlin"]


def test_a_second_call_comes_from_the_cache(monkeypatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    repo.get_materials()
    repo.get_materials()
    assert len(calls) == 1                    # no second HTTP round trip


def test_on_an_api_outage_the_last_known_state_is_served(monkeypatch) -> None:
    """A dashboard with slightly stale numbers is better than an empty one."""
    state = {"broken": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["broken"]:
            raise httpx.ConnectError("gone")
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    repo.get_materials()
    state["broken"] = True

    frame = repo.get_materials(force_reload=True)
    assert frame.height == 2                  # old state instead of a crash


def test_without_a_cache_the_error_is_passed_through(monkeypatch) -> None:
    """No silent empty table: the first failure has to be noticed."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone")

    monkeypatch.setattr(repo, "_client", _client(handler))
    with pytest.raises(DataProductError):
        repo.get_materials()


def test_distinct_values_for_the_filter_dropdowns(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_client",
                        _client(lambda r: httpx.Response(200, json=_envelope(API_ROWS))))
    assert repo.distinct_values("plant") == ["Werk Berlin", "Werk Köln"]
    assert repo.distinct_values("material_group") == ["Rohstoffe"]   # None drops out


def test_truncation_is_reported(monkeypatch, caplog) -> None:
    """A table that looks complete but is not has to be noticed.

    The API reports 120,000 rows but delivers a short page -- so the stock
    cannot be fetched completely. Without this hint the dashboard shows a
    plausible table with missing data, and the KPI tiles count too little
    as well.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(API_ROWS, total_count=120_000))

    monkeypatch.setattr(repo, "_client", _client(handler))
    with caplog.at_level("ERROR"):
        repo.get_materials()

    assert repo.truncation() == (2, 120_000)
    assert "truncated" in caplog.text.lower()


def test_no_notice_without_truncation(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_client",
                        _client(lambda r: httpx.Response(200, json=_envelope(API_ROWS))))
    repo.get_materials()
    assert repo.truncation() is None


# --- The copy must not drift away from the template -------------------------

def test_the_client_is_identical_to_the_template() -> None:
    """`data/api_client.py` is a copy of `api/.../dash_client.py`.

    Without this test, "if it changes over there it gets pulled in here" is a
    declaration of intent that breaks under the first bit of time pressure.

    What is compared is the AST without the module docstring: the two files may
    have different introductions and comments (the template explains the
    copying, the copy explains its origin), but no different behaviour.
    """
    import ast
    from pathlib import Path

    here = Path(__file__).resolve()
    copy = here.parents[1] / "data" / "api_client.py"
    template = here.parents[3] / "api" / "src" / "clients" / "dash_client.py"

    if not template.exists():                     # dashboard checked out without the monorepo
        pytest.skip(f"template not found: {template}")

    def body(path: Path) -> str:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        nodes = tree.body
        if (nodes and isinstance(nodes[0], ast.Expr)
                and isinstance(nodes[0].value, ast.Constant)
                and isinstance(nodes[0].value.value, str)):
            nodes = nodes[1:]                     # leave out the module docstring
        return "\n".join(ast.dump(n, indent=2) for n in nodes)

    assert body(copy) == body(template), (
        "data/api_client.py and api/src/clients/dash_client.py have "
        "drifted apart. Copy the template and adjust only the header."
    )


# --- Sign-in: the cache must not leak across users --------------------------

def test_the_cache_is_kept_separate_per_role_set(monkeypatch) -> None:
    """The process cache must not hand data to people who are not entitled.

    Without the role key the second user would get the first user's state out
    of the cache -- the API would never have been asked and its 403 therefore
    never raised. That is the kind of hole no test of the API itself can find,
    because it sits in the dashboard.
    """
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    monkeypatch.setattr(repo, "access_token", lambda: "token-planner")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset({"planner"}))
    repo.get_materials()
    repo.get_materials()                       # cached -> no second call
    assert len(calls) == 1

    # Different user, different roles -> own bucket, so off to the API again
    monkeypatch.setattr(repo, "access_token", lambda: "token-guest")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset({"guest"}))
    repo.get_materials()
    assert len(calls) == 2


def test_the_token_is_sent_along_as_a_bearer_header(monkeypatch) -> None:
    """Without this header the API answers with 401 -- and rightly so."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))
    monkeypatch.setattr(repo, "access_token", lambda: "abc.def.ghi")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset())
    repo.get_materials()
    assert seen == ["Bearer abc.def.ghi"]


def test_a_403_is_not_answered_from_the_cache(monkeypatch) -> None:
    """When the permission is missing, no old state may be handed out.

    The outage fallback ("stale numbers rather than an empty table") applies to
    an unreachable API -- not to one that deliberately says no.
    """
    responses = [httpx.Response(200, json=_envelope(API_ROWS)),
                 httpx.Response(403, json={"title": "Access denied", "detail": "nope",
                                           "code": "forbidden"})]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    monkeypatch.setattr(repo, "_client", _client(handler))
    monkeypatch.setattr(repo, "access_token", lambda: "t")
    monkeypatch.setattr(repo, "user_roles", lambda: frozenset({"planner"}))

    repo.get_materials()                                   # fills the cache
    with pytest.raises(NotAuthorisedError):
        repo.get_materials(force_reload=True)


# --- Invalidation after a write --------------------------------------------

def test_invalidate_forces_a_fresh_fetch_on_the_next_access(monkeypatch) -> None:
    """The API cache is cleared server-side on a write -- the cache here is not.
    Without this call the user does not see their own change for up to
    CACHE_TTL_SECONDS."""
    fetches = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(request.url.path)
        return httpx.Response(200, json=_envelope(API_ROWS))

    monkeypatch.setattr(repo, "_client", _client(handler))

    repo.get_materials()
    repo.get_materials()
    assert len(fetches) == 1, "the second access did not come from the cache"

    repo.invalidate()
    repo.get_materials()
    assert len(fetches) == 2


def test_invalidate_clears_the_buckets_of_every_role(monkeypatch) -> None:
    """If somebody writes a mapping, that concerns everyone -- not only the
    roles of the person writing."""
    monkeypatch.setattr(repo, "_client",
                        _client(lambda request: httpx.Response(200, json=_envelope(API_ROWS))))

    monkeypatch.setattr(repo, "user_roles", lambda: ["planner"])
    repo.get_materials()
    monkeypatch.setattr(repo, "user_roles", lambda: ["viewer"])
    repo.get_materials()
    assert len(repo._CACHE) == 2

    repo.invalidate()
    assert repo._CACHE == {}


# --- Page-by-page loading ----------------------------------------------------

def _rows(count: int) -> list[dict]:
    return [dict(API_ROWS[0], material_number=f"MAT-{number}") for number in range(count)]


def _paged_handler(all_rows: list[dict], seen: list | None = None, total: int | None = None):
    """Answers limit/offset the way the real API does."""
    def handler(request: httpx.Request) -> httpx.Response:
        limit = int(request.url.params["limit"])
        offset = int(request.url.params.get("offset", 0))
        if seen is not None:
            seen.append(offset)
        page = all_rows[offset:offset + limit]
        return httpx.Response(200, json=_envelope(page, total_count=total or len(all_rows)))
    return handler


def test_a_complete_result_needs_only_one_request(monkeypatch) -> None:
    """The normal case: if everything fits on one page, no paging happens."""
    seen: list[int] = []
    monkeypatch.setattr(repo, "_client", _client(_paged_handler(_rows(3), seen)))

    assert repo.get_materials().height == 3
    assert seen == [0]


def test_every_page_is_fetched_and_joined(monkeypatch) -> None:
    """Without this, rows are missing from the table, from the KPI tiles and
    from the filter dropdowns -- all three without an error message."""
    seen: list[int] = []
    monkeypatch.setattr(repo, "PAGE_SIZE", 2)
    monkeypatch.setattr(repo, "_client", _client(_paged_handler(_rows(5), seen)))

    frame = repo.get_materials()

    assert frame.height == 5
    assert seen == [0, 2, 4]
    assert frame["material_number"].to_list() == [f"MAT-{n}" for n in range(5)]
    assert repo.truncation() is None


def test_the_upper_limit_stops_and_reports_the_truncation(monkeypatch, caplog) -> None:
    """The upper limit protects the worker -- but it must not kick in silently."""
    monkeypatch.setattr(repo, "PAGE_SIZE", 2)
    monkeypatch.setattr(repo, "MAX_ROWS", 4)
    monkeypatch.setattr(repo, "_client", _client(_paged_handler(_rows(9))))

    with caplog.at_level("ERROR"):
        frame = repo.get_materials()

    assert frame.height == 4
    assert repo.truncation() == (4, 9)
    assert "truncated" in caplog.text.lower()


def test_if_the_stock_changes_between_two_pages_it_is_loaded_again(
    monkeypatch, caplog
) -> None:
    """Otherwise the table mixes two states: rows can show up twice or go
    missing, and nobody notices."""
    all_rows = _rows(5)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", 0))
        attempts.append(offset)
        # On the first run the second page reports a different stock.
        changed = len(attempts) == 2
        page = all_rows[offset:offset + 2]
        return httpx.Response(200, json=_envelope(page, total_count=6 if changed else 5))

    monkeypatch.setattr(repo, "PAGE_SIZE", 2)
    monkeypatch.setattr(repo, "_client", _client(handler))

    with caplog.at_level("WARNING"):
        frame = repo.get_materials()

    assert frame.height == 5
    assert attempts == [0, 2, 0, 2, 4], "it did not load again from the start"
    assert "trying again" in caplog.text.lower()


def test_if_the_second_attempt_also_fails_the_fallback_path_applies(monkeypatch) -> None:
    """RowCountChanged is a DataProductError -- without an old state in the
    cache the error has to be noticed instead of showing half a table."""
    all_rows = _rows(5)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        offset = int(request.url.params.get("offset", 0))
        page = all_rows[offset:offset + 2]
        return httpx.Response(200, json=_envelope(page, total_count=5 + len(calls)))

    monkeypatch.setattr(repo, "PAGE_SIZE", 2)
    monkeypatch.setattr(repo, "_client", _client(handler))

    with pytest.raises(DataProductError):
        repo.get_materials()
