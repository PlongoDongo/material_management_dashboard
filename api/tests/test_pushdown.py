"""
Query-side pagination: does the window really reach the database?

These tests exist because every bug this feature can have is SILENT. A product
that pages wrongly still answers 200 with plausible-looking rows; nobody
notices until a number in a report is off. So each test below pins one of the
five things that have to move together (see catalog/material_search_v1.py):

    filters in the query · stable ORDER BY · window in the query and not in the
    router · a COUNT for total_count · the window in the cache key
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeSources

from data_api.products.cache import cache
from data_api.products.catalog import material_search_v1 as ms1

SEARCH = "/api/v1/data-products/material-search/v1"


# --- The window reaches the query -------------------------------------------

def test_limit_and_offset_are_passed_to_cypher(client: TestClient,
                                               fake_sources: FakeSources) -> None:
    """The whole point. If these never arrive, the database still returns all
    100k rows and the only thing pushdown achieved is a second COUNT query."""
    client.get(SEARCH, params={"limit": 5, "offset": 10})

    page_query, page_parameters = fake_sources.calls[0]
    assert "SKIP $skip LIMIT $limit" in page_query
    assert page_parameters["skip"] == 10
    assert page_parameters["limit"] == 5


def test_the_router_does_not_slice_a_second_time(client: TestClient) -> None:
    """The classic double-slicing bug: the query returns rows 11-15, and the
    router then takes rows[10:15] OF THOSE -- empty. Page 1 would look fine,
    which is why this asserts on a LATER page."""
    body = client.get(SEARCH, params={"limit": 5, "offset": 10}).json()

    assert body["meta"]["row_count"] == 5
    assert len(body["data"]) == 5


def test_pages_do_not_overlap_and_cover_the_result(client: TestClient) -> None:
    """A stable ORDER BY is what makes SKIP/LIMIT mean anything. Without it the
    database may hand back a row on two pages and drop another entirely -- and
    the response looks perfectly normal either way."""
    seen: list[str] = []
    for offset in range(0, 20, 5):
        page = client.get(SEARCH, params={"limit": 5, "offset": offset}).json()["data"]
        seen.extend(row["material_number"] for row in page)

    assert len(seen) == len(set(seen)) == 20
    assert seen == sorted(seen)                 # ORDER BY m.nr, ascending


def test_an_offset_past_the_end_is_empty_but_still_reports_the_total(
    client: TestClient,
) -> None:
    """"Nothing on page 40 of 3" and "nothing at all" are different answers.
    The COUNT runs even for an empty page so a client can tell them apart."""
    body = client.get(SEARCH, params={"limit": 10, "offset": 5_000}).json()

    assert body["data"] == []
    assert body["meta"]["row_count"] == 0
    assert body["meta"]["total_count"] == 64


# --- total_count comes from COUNT, not from len(rows) -----------------------

def test_total_count_is_the_match_count_not_the_page_size(client: TestClient) -> None:
    """`len(rows)` is now the size of the window. Using it would report
    total_count=5 for a 64-row result, and the dashboard's truncation warning
    (frontend .../data/repository.py) would fire on every single page."""
    body = client.get(SEARCH, params={"limit": 5}).json()

    assert body["meta"]["row_count"] == 5
    assert body["meta"]["total_count"] == 64


def test_a_filter_shrinks_the_total_as_well_as_the_page(client: TestClient) -> None:
    """The COUNT has to see the SAME filter as the page query. If the two ever
    drift apart, total_count keeps reporting the unfiltered number and nothing
    in the response looks wrong."""
    unfiltered = client.get(SEARCH, params={"limit": 5}).json()["meta"]["total_count"]
    filtered = client.get(SEARCH, params={"limit": 5, "status": "Gesperrt"}).json()

    assert filtered["meta"]["total_count"] < unfiltered
    assert all(row["status"] == "Gesperrt" for row in filtered["data"])


def test_both_queries_get_identical_filter_arguments(client: TestClient,
                                                     fake_sources: FakeSources) -> None:
    """Guards the anti-drift construction directly: one shared filter fragment,
    one shared argument dict. The page query additionally carries the window."""
    client.get(SEARCH, params={"status": "Aktiv", "min_stock": 500, "limit": 3})

    (_page_query, page_parameters), (count_query, count_parameters) = fake_sources.calls[:2]
    assert count_query is ms1.CYPHER_COUNT

    window = {"skip", "limit"}
    assert {k: v for k, v in page_parameters.items() if k not in window} == count_parameters


# --- The filters really are in the query ------------------------------------

def test_every_declared_filter_appears_in_the_query() -> None:
    """The rule this product lives by: a filter in the params model that is NOT
    in the query would be applied to an already-truncated page -- short pages,
    status 200. This catches the omission when someone adds a field.
    """
    declared = set(ms1.MaterialSearchParams.model_fields) - {"limit", "offset", "sort"}
    for name in declared:
        assert f"${name}" in ms1._MATCH_AND_FILTER, f"{name} is declared but never filtered on"


def test_the_product_has_no_python_side_filtering() -> None:
    """`_row` maps, it does not decide. A `continue` or an `if ... in params`
    in there would mean rows are dropped after the window was cut."""
    import inspect

    source = inspect.getsource(ms1._row)
    assert "params" not in source
    assert "continue" not in source


@pytest.mark.parametrize(("filter_name", "value"), [
    ("status", "Aktiv"),
    ("plant_id", "W-KOE"),
    ("unclassified_only", "true"),
    ("min_stock", 5_000),
    ("search", "sensor"),
])
def test_each_filter_reaches_cypher_as_a_parameter(client: TestClient,
                                                   fake_sources: FakeSources,
                                                   filter_name: str,
                                                   value: object) -> None:
    """Passed by name, never spliced into the query text -- that would be an
    injection hole and would stop Neo4j reusing the plan."""
    client.get(SEARCH, params={filter_name: value, "limit": 5})

    _query, parameters = fake_sources.calls[0]
    assert parameters[filter_name] not in (None, False)


# --- Sorting ----------------------------------------------------------------

def test_sorting_goes_through_a_whitelist_not_the_request(client: TestClient,
                                                          fake_sources: FakeSources) -> None:
    """A property name cannot be a Cypher parameter, so the sort column is
    interpolated -- which is only safe because the value came out of _SORTS."""
    client.get(SEARCH, params={"sort": "stock", "limit": 3})

    query, _parameters = fake_sources.calls[0]
    assert "ORDER BY m.bestand DESC, m.nr" in query


def test_an_unknown_sort_column_is_rejected_before_it_reaches_the_query(
    client: TestClient,
) -> None:
    """`ORDER BY` is a favourite place for injection because it looks harmless.
    Pydantic's Literal rejects this with a 422; _SORTS is the second guard."""
    response = client.get(SEARCH, params={"sort": "m.nr; MATCH (n) DETACH DELETE n"})
    assert response.status_code == 422


def test_a_non_unique_sort_column_gets_a_tie_breaker() -> None:
    """Ordering by something non-unique alone is not a total order, and paging
    over a partial order lets rows move between pages."""
    for column, fragment in ms1._SORTS.items():
        if column != "material_number":
            assert fragment.endswith("m.nr"), f"{column} sorts without a tie-breaker"


# --- The cache key carries the window ---------------------------------------

def test_two_pages_are_two_cache_entries(client: TestClient,
                                         fake_sources: FakeSources) -> None:
    """Without the window in the key, page 2 would be served page 1's rows --
    a data bug, not a performance one."""
    cache.invalidate()
    first = client.get(SEARCH, params={"limit": 5, "offset": 0}).json()
    second = client.get(SEARCH, params={"limit": 5, "offset": 5}).json()

    assert second["meta"]["cache"] == "miss"          # not served from page 1's entry
    assert first["data"][0]["material_number"] != second["data"][0]["material_number"]


def test_the_same_page_twice_is_a_cache_hit(client: TestClient) -> None:
    """The window is part of the key, not the whole key -- caching still works."""
    cache.invalidate()
    assert client.get(SEARCH, params={"limit": 5}).json()["meta"]["cache"] == "miss"
    assert client.get(SEARCH, params={"limit": 5}).json()["meta"]["cache"] == "hit"


def test_an_unpaged_product_still_shares_one_entry_across_pages(
    client: TestClient,
) -> None:
    """The opposite case, to prove the flag is what decides. material-overview
    caches the complete result, so a second page comes out of the same entry
    without touching the database."""
    cache.invalidate()
    path = "/api/v1/data-products/material-overview/v3"
    assert client.get(path, params={"limit": 5, "offset": 0}).json()["meta"]["cache"] == "miss"
    assert client.get(path, params={"limit": 5, "offset": 5}).json()["meta"]["cache"] == "hit"


def test_a_cache_hit_reports_the_total_it_was_stored_with(client: TestClient) -> None:
    """`total` goes into the cache alongside the rows. Recomputing it on a hit
    is impossible -- no query ran -- and len(rows) would give the page size."""
    cache.invalidate()
    client.get(SEARCH, params={"limit": 5})
    hit = client.get(SEARCH, params={"limit": 5}).json()

    assert hit["meta"]["cache"] == "hit"
    assert hit["meta"]["total_count"] == 64
