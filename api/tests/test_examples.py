"""
The four templates in catalog/example_*.py.

They exist to be copied, which makes them a different kind of code: a bug in one
gets reproduced, not just suffered. So each template is checked for the property
it is meant to demonstrate, and the ladder as a whole is checked for actually
being a ladder -- four steps that differ in exactly the documented way.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from products.cache import cache
from products.catalog import example_1_plain as ex1
from products.catalog import example_2_paged as ex2
from products.catalog import example_3_filtered as ex3
from products.catalog import example_4_full as ex4
from products.registry import registry

BASE = "/api/v1/data-products"
PLAIN = f"{BASE}/example-1-plain/v1"
PAGED = f"{BASE}/example-2-paged/v1"
FILTERED = f"{BASE}/example-3-filtered/v1"
FULL = f"{BASE}/example-4-full/v1"

TOTAL_MATERIALS = 64


# --- The ladder is a ladder -------------------------------------------------

def test_each_template_is_registered_and_reachable(client: TestClient) -> None:
    for path in (PLAIN, PAGED, FILTERED, FULL):
        assert client.get(path, params={"limit": 2}).status_code == 200, path


def test_the_four_steps_differ_exactly_as_documented() -> None:
    """The table in the developer guide, asserted.

    If a template quietly grows a capability it does not advertise, the ladder
    stops teaching what it claims to.
    """
    steps = {
        "example-1-plain": (False, False),
        "example-2-paged": (False, True),
        "example-3-filtered": (True, False),
        "example-4-full": (True, True),
    }
    for name, (has_filters, pages_in_query) in steps.items():
        product = registry.get(name, 1)
        assert product is not None, name

        own_fields = set(product.params_model.model_fields) - {"limit", "offset"}
        assert bool(own_fields) is has_filters, f"{name}: filters mismatch"
        assert product.paginated_by_source is pages_in_query, f"{name}: paging mismatch"


def test_only_template_4_transforms() -> None:
    """Templates 1-3 hand the records through untouched -- that is the whole
    reason their queries alias onto the contract's field names."""
    for module in (ex1, ex2, ex3):
        assert not hasattr(module, "transform"), module.__name__
    assert callable(ex4.transform)


# --- Template 1: everything, sliced by the router ---------------------------

def test_plain_returns_everything_and_the_router_slices(client: TestClient) -> None:
    body = client.get(PLAIN, params={"limit": 5, "offset": 10}).json()

    assert body["meta"]["row_count"] == 5
    assert body["meta"]["total_count"] == TOTAL_MATERIALS
    assert body["meta"]["product"] == "example-1-plain"


def test_plain_needs_no_params_model_of_its_own(client: TestClient) -> None:
    """limit/offset come from ProductParams whether a product declares them or
    not -- which is why template 1 has no params model at all."""
    assert client.get(PLAIN, params={"limit": 3}).json()["meta"]["row_count"] == 3
    assert client.get(PLAIN, params={"status": "Aktiv"}).status_code == 422


# --- Template 2: the window reaches the query -------------------------------

def test_paged_sends_the_window_to_cypher(client: TestClient, fake_sources) -> None:  # noqa: ANN001
    client.get(PAGED, params={"limit": 5, "offset": 10})

    page_query, parameters = fake_sources.calls[0]
    assert "SKIP $offset LIMIT $limit" in page_query
    assert (parameters["offset"], parameters["limit"]) == (10, 5)


def test_paged_pages_do_not_overlap(client: TestClient) -> None:
    """A stable ORDER BY is what makes SKIP/LIMIT mean anything: without it the
    database may return a row on two pages and drop another entirely."""
    seen: list[str] = []
    for offset in range(0, 20, 5):
        page = client.get(PAGED, params={"limit": 5, "offset": offset}).json()["data"]
        seen.extend(row["material_number"] for row in page)

    assert len(seen) == len(set(seen)) == 20
    assert seen == sorted(seen)


def test_paged_reports_the_total_not_the_page_size(client: TestClient) -> None:
    body = client.get(PAGED, params={"limit": 5}).json()

    assert body["meta"]["row_count"] == 5
    assert body["meta"]["total_count"] == TOTAL_MATERIALS


def test_paged_keeps_one_cache_entry_per_page(client: TestClient) -> None:
    """Without the window in the cache key, page 2 would be served page 1's rows."""
    cache.invalidate()
    first = client.get(PAGED, params={"limit": 5, "offset": 0}).json()
    second = client.get(PAGED, params={"limit": 5, "offset": 5}).json()

    assert second["meta"]["cache"] == "miss"
    assert first["data"][0] != second["data"][0]


# --- Template 3: filters in the query, no window in the query ---------------

def test_filtered_narrows_in_cypher(client: TestClient, fake_sources) -> None:  # noqa: ANN001
    body = client.get(FILTERED, params={"status": "Gesperrt"}).json()

    _query, parameters = fake_sources.calls[0]
    assert parameters["status"] == ["Gesperrt"]
    assert all(row["status"] == "Gesperrt" for row in body["data"])
    assert body["meta"]["total_count"] < TOTAL_MATERIALS


def test_filtered_does_not_send_a_window_to_the_query(
    client: TestClient, fake_sources,  # noqa: ANN001
) -> None:
    """The router slices here, so passing limit/offset to a query that never
    references them would only mislead the next reader."""
    client.get(FILTERED, params={"limit": 5})

    query, parameters = fake_sources.calls[0]
    assert "SKIP" not in query
    assert "limit" not in parameters and "offset" not in parameters


def test_filtered_still_reports_the_full_match_count(client: TestClient) -> None:
    """No COUNT query needed: the loader returned every match, so the router
    knows the total by counting what it was given."""
    body = client.get(FILTERED, params={"limit": 3, "min_stock": 5000}).json()

    assert body["meta"]["row_count"] == 3
    assert body["meta"]["total_count"] > 3


def test_an_empty_multi_select_means_no_filter(client: TestClient) -> None:
    """`status=[]` must mean "no filter", not "match nothing" -- otherwise an
    empty dropdown in a dashboard silently empties the table."""
    everything = client.get(FILTERED).json()["meta"]["total_count"]
    assert everything == TOTAL_MATERIALS


# --- Template 4: all of it, and the rule that makes it fragile --------------

def test_full_computes_without_dropping_rows(client: TestClient) -> None:
    body = client.get(FULL, params={"limit": 5}).json()

    assert body["meta"]["row_count"] == 5
    assert body["meta"]["total_count"] == TOTAL_MATERIALS
    for row in body["data"]:
        assert row["stock_class"] in {"unknown", "low", "medium", "high"}


def test_the_transform_computes_but_never_filters() -> None:
    """THE rule of template 4, and the reason this file exists.

    `transform()` runs after the window was cut, so a filter in there filters a
    single page: LIMIT 20 fetches 20 rows, the transform drops 17, the client
    sees 3 and concludes there is no more data -- while total_count still
    reports the unfiltered number. Nothing errors.
    """
    source = inspect.getsource(ex4.transform)
    for smell in ("continue", "if params", "params.", "filter("):
        assert smell not in source, f"{smell!r} in transform() -- that belongs in the query"

    rows = [{"stock": 10, "price": 2.0}, {"stock": None, "price": None}]
    assert len(ex4.transform(rows)) == 2, "transform() dropped a row"


def test_unknown_stays_unknown_rather_than_zero() -> None:
    """Rounding "we do not know" down to 0 puts a wrong number into a sum that
    nobody questions afterwards."""
    [row] = ex4.transform([{"stock": None, "price": 5.0}])

    assert row["stock_value"] is None
    assert row["stock_class"] == "unknown"


def test_full_uses_one_filter_for_both_queries(client: TestClient, fake_sources) -> None:  # noqa: ANN001
    """The anti-drift construction: page and COUNT share a query fragment and an
    argument dict, so they cannot disagree about what "matching" means."""
    client.get(FULL, params={"status": "Aktiv", "min_stock": 500, "limit": 3})

    (_page, page_parameters), (count_query, count_parameters) = fake_sources.calls[:2]
    assert count_query is ex4.CYPHER_COUNT

    window = {"offset", "limit"}
    assert {k: v for k, v in page_parameters.items() if k not in window} == count_parameters
    assert window.isdisjoint(count_parameters)


def test_full_sorting_goes_through_the_whitelist(client: TestClient, fake_sources) -> None:  # noqa: ANN001
    client.get(FULL, params={"sort": "stock", "limit": 3})

    query, _parameters = fake_sources.calls[0]
    assert "ORDER BY m.bestand DESC, m.nr" in query


def test_an_injected_sort_column_never_reaches_the_query(client: TestClient) -> None:
    """`ORDER BY` has to be interpolated, so the Literal is what keeps it safe."""
    response = client.get(FULL, params={"sort": "m.nr; MATCH (n) DETACH DELETE n"})
    assert response.status_code == 422


@pytest.mark.parametrize("module", [ex2, ex4])
def test_every_paged_template_sorts_on_something_unique(module) -> None:  # noqa: ANN001
    """SKIP/LIMIT over a partial order is undefined. Non-unique sort columns
    need m.nr as a tie-breaker."""
    if hasattr(module, "_SORTS"):
        for column, fragment in module._SORTS.items():
            if column != "material_number":
                assert fragment.endswith("m.nr"), f"{column} sorts without a tie-breaker"
    else:
        assert "ORDER BY m.nr" in module.CYPHER_PAGE


# --- What every template must get right -------------------------------------

@pytest.mark.parametrize("module", [ex1, ex2, ex3, ex4])
def test_every_declared_filter_appears_in_its_query(module) -> None:  # noqa: ANN001
    """A field on the params model that no query references is silently ignored
    -- the caller filters and gets everything back."""
    product = next(p for p in registry.all() if p.loader.__module__ == module.__name__)
    declared = set(product.params_model.model_fields) - {"limit", "offset", "sort"}

    queries = " ".join(
        value for name, value in vars(module).items()
        if isinstance(value, str) and name.isupper() or name == "_MATCH_AND_FILTER"
    )
    for name in declared:
        assert f"${name}" in queries, f"{module.__name__}: {name} is declared but never used"


@pytest.mark.parametrize("module", [ex1, ex2, ex3, ex4])
def test_every_template_aliases_onto_its_contract(module) -> None:  # noqa: ANN001
    """The queries RETURN the contract's field names, which is what makes the
    records usable as rows without a mapping function in between."""
    product = next(p for p in registry.all() if p.loader.__module__ == module.__name__)
    computed = {"stock_value", "stock_class"}          # template 4 adds these itself

    queries = " ".join(v for v in vars(module).values() if isinstance(v, str))
    for name in set(product.item_model.model_fields) - computed:
        assert f"AS {name}" in queries, f"{module.__name__}: {name} is never returned"
