# API layer (FastAPI)

The layer between the Dash dashboards and the data sources (Neo4j, Postgres, and
further services later on).

This README is the short version to get started. In more depth:

| Document | Purpose |
|---|---|
| [`docs/api_development_guide.md`](../docs/api_development_guide.md) | **Developer guide** — recipes for extending it, conventions, review checklist |
| [`docs/api_layer_concept.md`](../docs/api_layer_concept.md) | Concept and the reasoning behind the architectural decisions |
| [`docs/api_grundlagen.md`](../docs/api_grundlagen.md) | Background for newcomers — what is a router, a session, async? Includes the history (German) |

---

## Quick start

The **tests** run without a database (test doubles via `dependency_overrides`).
The **server** needs its sources: without `NEO4J_URI` or `POSTGRES_DSN` the
affected data products report a configuration error and `/readyz` answers 503.

For sources that do not exist yet, create mock data **in the database** rather
than in the code — see [`seed/`](seed/).

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m main
```

```bash
.venv/bin/python -m pytest -q
```

Then in the browser: <http://localhost:8000/docs>

```bash
curl -s localhost:8000/api/v1/catalog | python -m json.tool
```

---

## The core ideas in 60 seconds

**Data product** — a named, versioned contract over a dataset. Not "a route that
happens to query the database". It has a schema, a cache duration and a
lifecycle.

**Two version axes** — easy to confuse:

```
/api/v1/data-products/material-overview/v3
 ^^^^^^                                 ^^
 API version (transport:                data product version
 error format, auth, envelope)          (fields of this dataset)
```

The path carries only the **MAJOR** (`v3`); the full version (`3.0`) is in
`meta.version` of the response. Field added → MINOR, same route. Field removed,
renamed, retyped **or its meaning changed** → MAJOR, new route, the old one
stays until its sunset date.

**Driver vs. session** — drivers and engines live for the whole process
(lifespan), sessions for exactly one request (depends). Never the other way
round.

---

## Directories

| Path | Contents |
|---|---|
| `src/products/catalog/` | **New data products go here** — one file per product and major version |
| `src/products/` | The framework: registry, route generator, cache, base models |
| `src/db/` | Driver/engine lifecycle and `Sources` (`sources.neo4j(...)`) per request |
| `src/api/v1/` | Hand-written routers (health, catalog, write side) |
| `src/core/` | Settings, logging, error format, auth |
| `src/clients/` | Client template for the Dash apps |
| `seed/` | Mock data for Neo4j/Postgres while sources are missing |
| `tests/fakes.py` | Test doubles — the only sample data in the repository |

---

## Endpoints

```
GET   /api/v1/healthz                                  liveness (process only)
GET   /api/v1/readyz                                   readiness (checks the sources)
GET   /api/v1/catalog                                  all data products + versions
GET   /api/v1/catalog/{name}                           one product in detail
GET   /api/v1/data-products/{name}/v{major}            the data
GET   /api/v1/data-products/{name}/latest              alias (not for dashboards!)
POST  /api/v1/mappings                                 write-side example
PATCH /api/v1/mappings/{id}
```

Response format of every data product:

```json
{
  "meta": {"product": "...", "version": "3.0", "generated_at": "...",
           "row_count": 64, "total_count": 64, "source": "neo4j",
           "cache": "hit", "deprecated": false, "sunset": null},
  "data": [ ... ]
}
```

`meta.source` shows which sources fed the response.

Errors use RFC 9457 Problem Details (`application/problem+json`) — one shape for
every failure, with a `code` to branch on and the request id:

```json
{"type": "about:blank", "title": "Upstream data source unavailable", "status": 503,
 "detail": "Neo4j unavailable: no route to host", "code": "upstream_unavailable",
 "request_id": "3f2a9c1b4d5e6f70"}
```

`/docs` lists them per route (401, 403, 409, 422, 500, 503), declared through
`documented_errors(...)` in `core/errors.py`, which reads them off the error classes.

Every error response carries its own example — a 401 is illustrated with a 401,
not with a generic one.

> **Known quirk in `/docs`:** each error is listed under `application/json` as
> well, and Swagger preselects that one. FastAPI attaches a declared response
> model to the route's default media type and offers no per-response way to
> change it; removing the extra entry would need a custom `openapi()` hook.
> Both entries carry the same schema and example, and the API always sends
> `application/problem+json`.

---

## Adding a data product

One file in `src/products/catalog/`, nothing else:

```python
CYPHER = """MATCH (m:Material)-[:LOCATED_IN]->(p:Werk) RETURN ..."""

async def load(sources: Sources, params: PlantParams):
    return transform(await sources.neo4j(CYPHER), params)

registry.add(DataProduct(
    name="plant-utilisation", version="1.0",
    summary="Materials and stock per plant",
    item_model=PlantRow, params_model=PlantParams, loader=load,
    cache_ttl=120,
))
```

After a restart you automatically get: the route, the `/latest` alias, a full
OpenAPI entry with schema, a catalog entry, caching, ETag, pagination, the error
format and the auth check. No router is touched.

Convention per file — always in this order:

1. **`CYPHER` / `SQL`** → the query
2. **row model** → the contract
3. **params model** → the allowed filters
4. **`transform()`** → pure function, no I/O — this is where the domain logic lives
5. **`load()`** → fetches the raw rows, calls `transform()`
6. **`registry.add(...)`** → publishes the product

The split between 4 and 5 is why the domain logic is testable without a database
(`tests/test_transformations.py`). One file = one data product = everything about
it; there is deliberately no separate repository layer.

Then add the query to `tests/fakes.py` so `FakeSources` knows how to answer it.

---

## Configuration

`cp .env.example .env` and fill it in. The Neo4j variables deliberately match
the dashboard's — the same `.env` works for both.

| Variable | Meaning |
|---|---|
| `NEO4J_URI`, `NEO4J_AUTH`, `NEO4J_DB` | required — missing means 503 on `/readyz` |
| `POSTGRES_DSN` | must be `postgresql+asyncpg://` (async driver) |
| `API_CORS_ORIGINS` | ports of the Dash apps, comma-separated |
| `API_KEYS` | empty = auth disabled (development only) |

> Comments belong on their own line, never after a value: `python-dotenv` only
> strips a trailing comment when a value precedes it, so `API_KEYS=  # empty`
> would read the comment as a key and switch auth on.

---

## Connecting a dashboard

`src/clients/dash_client.py` is the template. In the dashboard only
`data/repository.py` changes:

```python
from data.api_client import DataProductClient
_client = DataProductClient()          # once per process

def load_materials() -> pl.DataFrame:
    rows, meta = _client.fetch("material-overview", "v3", limit=50_000)
    return _rows_to_frame(rows)
```

The version is pinned explicitly rather than using `latest`: a version change
should show up in the git diff, not happen silently.

---

## Mock data for missing sources

```bash
python seed/seed_neo4j.py            # create (every node gets the :Mock label)
python seed/seed_neo4j.py --purge    # remove again
psql "$DSN" -f seed/seed_postgres.sql
```

Details in [`seed/README.md`](seed/README.md).

---

## Tests

```
tests/test_transformations.py   domain logic only, no DB, no HTTP  <- most tests
tests/test_registry.py          registry rules (version collisions etc.)
tests/test_data_products.py     end-to-end over HTTP
tests/test_operations.py        request ids, cache metadata, auth, readiness
tests/test_health.py            operational endpoints
tests/test_write_routes.py      every write route has a role and an invalidation
tests/test_integration_neo4j.py against a REAL database (skipped without NEO4J_URI)
tests/fakes.py                  test doubles (a tool, not a test)
```

Everything except the integration tests runs without a database and without
Docker: `conftest.py` injects a fake via
`app.dependency_overrides[get_sources]`. Only the bottom layer is replaced —
everything above it runs for real.
