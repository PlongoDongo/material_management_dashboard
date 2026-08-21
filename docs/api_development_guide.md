# API Layer — Developer Guide

How this service is built and how to extend it.

**Audience:** anyone adding a data product, a data source, or an endpoint.
**Prerequisites:** Python, basic HTTP. FastAPI knowledge helps but is not required —
the patterns below are the ones you actually need.

Related documents:
* [`api_layer_concept.md`](api_layer_concept.md) — the design rationale and trade-offs.
* [`architecture.md`](architecture.md) — auto-generated diagrams of the current state.
* [`api_grundlagen.md`](api_grundlagen.md) — background for readers new to these patterns (German).

---

## Contents

1. [What this service is](#1-what-this-service-is)
2. [Mental model in five minutes](#2-mental-model-in-five-minutes)
3. [Repository layout](#3-repository-layout)
4. [The four rules](#4-the-four-rules)
5. [Local setup](#5-local-setup)
6. [Recipe: add a data product](#6-recipe-add-a-data-product)
7. [Recipe: release a breaking change](#7-recipe-release-a-breaking-change)
8. [Recipe: add a data source](#8-recipe-add-a-data-source)
9. [Recipe: add a write endpoint](#9-recipe-add-a-write-endpoint)
10. [Testing](#10-testing)
11. [Configuration](#11-configuration)
12. [Errors](#12-errors)
13. [Caching](#13-caching)
14. [Architecture docs](#14-architecture-docs)
15. [Review checklist](#15-review-checklist)
16. [Pitfalls](#16-pitfalls)

---

## 1. What this service is

A read-mostly gateway between the Plotly Dash dashboards and our data sources
(Neo4j, Postgres, later others). It exists so that:

* dashboards never hold database credentials or write Cypher,
* a metric is computed **once**, not once per dashboard,
* the graph model can change without touching every consumer,
* access control has a single place to live.

**What it is not:** a generic "database over HTTP" service. There is no endpoint
that accepts arbitrary Cypher. That would move the problem rather than solve it,
and it makes caching, authorisation and versioning impossible.

---

## 2. Mental model in five minutes

### A data product is a contract, not a route

```
name          material-overview          stable business name
version       2.0                        MAJOR.MINOR
item_model    MaterialRowV2              THE contract: fields, types, optionality
params_model  MaterialParamsV2           allowed filters, typed
loader        async (repos, params)      query + transformation
owner         team-material-management   who to ask
cache_ttl     60                         how fresh it must be
```

Declare it once in `products/catalog/`; the registry turns it into a typed route,
an OpenAPI entry, a catalog entry, caching and ETag support. You never write a
router for a data product.

### Two independent version axes

```
/api/v1/data-products/material-overview/v2
 ^^^^^^                                 ^^
 API version                            data product version
 transport contract:                    data contract:
 error format, auth, envelope           the fields of THIS dataset
```

They change independently. Adding a field to one product must not bump the whole
API; changing the auth mechanism must not re-version every product.

### Layers, and one rule about them

```
Router      transport: paths, status codes, headers, OpenAPI
Product     business logic: which sources, which formula
Repository  data access: Cypher, SQL
Infra       drivers, pools, lifecycle
```

Dependencies only ever point **down**. A repository knows nothing about FastAPI.
A `transform()` function knows nothing about databases. That is why the business
logic is testable in milliseconds.

---

## 3. Repository layout

```
api/
├── src/data_api/
│   ├── main.py                 uvicorn entry point
│   ├── application.py          create_app() + lifespan
│   ├── architecture.py         generates docs/architecture.md
│   │
│   ├── core/                   cross-cutting, no business logic
│   │   ├── config.py           Settings (pydantic-settings)
│   │   ├── logging.py          logging + request-id context
│   │   ├── middleware.py       request id, timing
│   │   ├── errors.py           AppError hierarchy + Problem Details
│   │   └── security.py         Principal, auth dependency
│   │
│   ├── db/
│   │   ├── neo4j.py            driver lifecycle
│   │   ├── sql.py              engine + sessionmaker
│   │   └── repositories.py     Repositories container (per request)
│   │
│   ├── repositories/           port (Protocol) + adapter, per business area
│   │   ├── materials.py
│   │   └── deliveries.py
│   │
│   ├── products/               the data product framework
│   │   ├── base.py             DataProduct, envelope, meta, params
│   │   ├── registry.py         registry, @data_product, auto-discovery
│   │   ├── router.py           builds typed routes from the registry
│   │   ├── cache.py            TTL cache + ETag
│   │   └── catalog/            ← YOUR NEW PRODUCT GOES HERE
│   │
│   ├── api/                    hand-written routers
│   │   ├── deps.py
│   │   └── v1/                 health, catalog, mappings
│   │
│   └── clients/dash_client.py  template for the Dash apps
├── seed/                       mock data for sources we do not have yet
├── tests/
│   └── fakes.py                test doubles — the only sample data in the repo
└── tools/validate_mermaid.mjs  optional: check generated diagrams
```

---

## 4. The four rules

**1. Dependencies point down.** `repositories/` must not import from `products/`
or `api/`. `products/` must not import FastAPI. If you need something from a
layer above, you are solving the problem in the wrong place.

**2. Never raise `HTTPException` outside `api/` and `products/router.py`.**
Business code raises an `AppError` subclass (`core/errors.py`). Those know
nothing about HTTP and are testable without a web server. The translation to
status codes happens in one place.

**3. Keep `transform()` pure.** No I/O, no `Repositories`, no `Request`. It takes
rows and params, returns rows. This is where the bugs live, so it must be
testable in milliseconds.

**4. No sample data in `src/`.** Anything shipped can run in production. Mock
data for missing sources goes into the database (`seed/`); data for tests goes
into `tests/fakes.py`.

---

## 5. Local setup

```bash
cd api
uv venv && uv pip install -e ".[dev]"
cp .env.example .env          # fill in the Neo4j / Postgres coordinates
```

```bash
.venv/bin/uvicorn data_api.main:app --reload --port 8000
```

```bash
.venv/bin/python -m pytest -q       # runs without any database
```

Interactive docs: <http://localhost:8000/docs>

If a source you need does not exist yet, seed it rather than faking it in code:

```bash
python seed/seed_neo4j.py           # every node gets an extra :Mock label
psql "$DSN" -f seed/seed_postgres.sql
```

---

## 6. Recipe: add a data product

One file in `src/data_api/products/catalog/`. Nothing else — no router, no
registration list, no `main.py` change.

Naming convention: `<product_name>_v<major>.py`.

Always in this order — predictability beats elegance when several teams
contribute products:

```python
"""werk-auslastung v1 — materials and stock per plant."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from data_api.db.repositories import Repositories
from data_api.products.base import ProductParams
from data_api.products.registry import data_product


# 1. THE CONTRACT ─ what consumers may rely on.
class WerkRow(BaseModel):
    werk_id: str
    werk_name: str | None = None
    materialien: int = Field(description="Number of distinct materials.")
    bestand_gesamt: int


# 2. THE FILTERS ─ everything a caller may pass as a query parameter.
class WerkParams(ProductParams):
    min_materialien: int = Field(0, ge=0)


# 3. THE BUSINESS LOGIC ─ pure. No database, no FastAPI. Test this.
def transform(rows: list[dict[str, Any]], params: WerkParams) -> list[dict[str, Any]]:
    per_werk: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = per_werk.setdefault(row["werk_id"], {
            "werk_id": row["werk_id"], "werk_name": row["werk_name"],
            "materialien": 0, "bestand_gesamt": 0,
        })
        entry["materialien"] += 1
        entry["bestand_gesamt"] += row.get("bestand") or 0
    return [w for w in per_werk.values() if w["materialien"] >= params.min_materialien]


# 4. THE WIRING ─ fetch raw rows, call transform. Keep this boring.
@data_product(
    name="werk-auslastung",
    version="1.0",
    summary="Materials and stock per plant",
    item_model=WerkRow,
    params_model=WerkParams,
    owner="team-material-management",
    tags=("werk", "aggregat"),
    cache_ttl=120,
)
async def load(repos: Repositories, params: WerkParams) -> list[dict[str, Any]]:
    """Aggregated key figures per plant."""
    repo = await repos.materials()
    return transform(await repo.fetch_materials(), params)
```

After a restart you automatically get:

* `GET /api/v1/data-products/werk-auslastung/v1`
* the `/latest` alias
* a full OpenAPI entry with schema under `/docs`
* a catalog entry under `/api/v1/catalog`
* caching, ETag, pagination, error format, auth check

Then:

```bash
.venv/bin/architecture-docs        # regenerate the diagrams
.venv/bin/python -m pytest -q
```

### Choosing `cache_ttl`

| Data changes | TTL | Example |
|---|---|---|
| Master data, hourly at most | `300`–`900` | supplier risk |
| Operational, minutes | `60` | material overview |
| Must be immediate after a write | `0` | anything a user edits and re-reads |

Remember to call `cache.invalidate("<product>")` from any write endpoint that
changes the underlying data.

### Where to filter

Filter as early as possible. Ideally the parameters end up in the Cypher/SQL
`WHERE` clause rather than in Python after loading two million rows. The current
products filter after loading because the datasets are small; moving that into
the query is a change local to one repository method plus one `transform()`.

---

## 7. Recipe: release a breaking change

| Change | Version | Route | Consumer impact |
|---|---|---|---|
| Field added | MINOR (`2.0` → `2.1`) | unchanged `/v2` | none — unknown fields are ignored |
| Docs, performance, bugfix without formula change | MINOR | unchanged | none |
| Field removed or renamed | MAJOR → `/v3` | new route | migration required |
| Field type changed | MAJOR → `/v3` | new route | migration required |
| **Meaning** of a field changed (formula!) | MAJOR → `/v3` | new route | migration required |

The last row is the dangerous one: if the calculation behind `risiko_score`
changes, the schema is identical but the numbers mean something else. That is a
breaking change even though no type moved.

Procedure:

1. Copy `product_v2.py` to `product_v3.py`, change `version="3.0"`, make the change.
2. Mark the old one deprecated and give it a sunset date:

   ```python
   @data_product(name="...", version="2.4", ..., deprecated=True,
                 sunset=date(2027, 6, 30))
   ```

   This automatically emits `Deprecation: true` and `Sunset: …` response headers,
   and the Dash client logs a warning when it consumes the product.
3. Tell the consuming teams; the catalog (`/api/v1/catalog/<name>`) lists both.
4. After the sunset date, check the access logs, then **delete** `product_v2.py`.
   Route, docs and catalog entry disappear with it.

**Do not patch a version that consumers still use.** "We'll just fix v2 quickly,
only one dashboard uses it" is the standard way versioning fails.

---

## 8. Recipe: add a data source

Say we add a REST service for quality data.

**Step 1 — port and adapter** in `repositories/quality.py`:

```python
@runtime_checkable
class QualityRepository(Protocol):
    source: str
    async def fetch_defect_rates(self, seit: dt.date) -> list[dict[str, Any]]: ...


class HttpQualityRepository:
    source = "quality-service"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_defect_rates(self, seit: dt.date) -> list[dict[str, Any]]:
        response = await self._client.get("/defect-rates", params={"since": seit.isoformat()})
        response.raise_for_status()
        return response.json()["items"]
```

**Step 2 — lifecycle** in `application.py`, if it needs a long-lived connection
pool. Long-lived objects belong in the lifespan, never in a module global:

```python
app.state.quality_client = httpx.AsyncClient(base_url=settings.quality_url)
...
await app.state.quality_client.aclose()
```

**Step 3 — one method** on the `Repositories` container in `db/repositories.py`:

```python
async def quality(self) -> QualityRepository:
    if self._quality_client is None:
        raise ConfigurationError("QUALITY_URL is not configured.")
    self.sources_used.add(HttpQualityRepository.source)
    return HttpQualityRepository(self._quality_client)
```

**Step 4 — use it.** `await repos.quality()` in any product loader.

The architecture diagram picks the new source up automatically: it reads the
`return` statements of the container methods and the `source` attribute of the
adapters. Nothing to maintain by hand.

---

## 9. Recipe: add a write endpoint

Write endpoints are **not** data products and do not go through the registry.
Reads are a contract about the *shape* of data; writes are a contract about an
*action*, with preconditions, side effects and transactions. Put them in a
hand-written router under `api/v1/`.

See `api/v1/mappings.py` for the reference. Three conventions:

**Separate input and output models.** The client must not set `id` or
`geaendert_am`. Two small models beat one large model with exceptions.

**Pick the right method.** There is no `UPDATE` in HTTP:

| Method | Meaning | Idempotent |
|---|---|---|
| `POST` | create, or trigger an action | no |
| `PUT` | replace the whole record | yes |
| `PATCH` | change only the fields sent | yes |
| `DELETE` | remove | yes |

**Invalidate the cache.** Otherwise the dashboard shows stale data for up to
`cache_ttl` seconds and the user thinks the save failed:

```python
cache.invalidate("material-overview")
```

Register the router in `api/v1/__init__.py` — the one place that assembles them.

---

## 10. Testing

```
tests/test_transformations.py   pure business logic, no DB, no HTTP  ← most tests
tests/test_registry.py          registry rules (version collisions, latest)
tests/test_data_products.py     end-to-end over HTTP
tests/test_health.py            operational endpoints
tests/test_architecture.py      diagram generator + staleness check
tests/fakes.py                  test doubles (a tool, not a test)
```

Everything runs without a database. Two mechanisms make that work:

**App factory.** `create_app(settings)` returns a fresh app with explicit
configuration — no environment variables, no monkeypatching.

**`dependency_overrides`.** `conftest.py` swaps `get_repositories` for
`FakeRepositories`. Only the bottom layer is replaced; route, validation, loader,
transformation, envelope, cache and headers all run for real.

```python
def test_something(client):                 # app + fake repositories, ready to go
    body = client.get("/api/v1/data-products/werk-auslastung/v1").json()
    assert body["meta"]["source"] == "fake"
```

Use `client_ohne_datenquellen` when you want the app **without** the override —
that fixture verifies what happens when a source is genuinely missing.

**When you add a data product, add at minimum:**

1. Tests for `transform()` covering the interesting cases — empty input, `None`
   values, each filter, the boundary of any threshold.
2. One HTTP test asserting the contract fields are present.

`None` vs `0` deserves particular attention: a missing stock level is *unknown*,
not *empty*. Silently turning it into `0` produces plausible, wrong dashboards.

---

## 11. Configuration

All settings live in `core/config.py` (pydantic-settings). Add a field there
rather than calling `os.getenv()` somewhere in the code — typed settings fail at
startup instead of in the first request.

| Variable | Meaning |
|---|---|
| `NEO4J_URI`, `NEO4J_AUTH`, `NEO4J_DB` | required for graph-backed products |
| `POSTGRES_DSN` | must be `postgresql+asyncpg://` — async driver |
| `API_ENV` | `dev` / `staging` / `prod` |
| `API_CORS_ORIGINS` | comma-separated origins of the Dash apps |
| `API_KEYS` | comma-separated; empty disables auth (dev only) |
| `API_LOG_LEVEL` | `INFO` by default |

Secrets belong in the platform's secret store, not in a committed `.env`.
`.env.example` is what gets committed.

The variable names match the dashboards' on purpose — the same `.env` works for
both.

---

## 12. Errors

One format for the whole API — [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457):

```json
{"type": "about:blank", "title": "Invalid request", "status": 422,
 "detail": "…", "code": "validation_error", "request_id": "1fca65e7ef6b"}
```

Raise the right one from `core/errors.py`:

| Exception | Status | When |
|---|---|---|
| `ProductNotFoundError` | 404 | unknown product or version |
| `UpstreamUnavailableError` | 503 | Neo4j/Postgres unreachable — "retry later" |
| `ConfigurationError` | 500 | a required source is not configured |
| `AppError` | 500 | anything else that is genuinely our bug |

503 vs 500 matters to consumers: the first means "try again", the second means
"file a bug". Every response carries an `X-Request-ID` that also appears in every
log line for that request.

---

## 13. Caching

Cache key is `(product, major, parameters)`. Different filters are different
answers — getting this wrong is the classic cache bug.

The cache is **in-process**. With multiple uvicorn workers each worker has its
own; hit rate drops, correctness does not. Swap `TTLCache` for Redis when that
starts to matter — the `get`/`set`/`invalidate` interface stays the same.

`ETag` + `If-None-Match` gives polling dashboards a `304 Not Modified` with no
body when nothing changed. `generated_at` is deliberately excluded from the ETag,
otherwise it would change on every request.

---

## 14. Architecture docs

```bash
.venv/bin/architecture-docs            # writes ../docs/architecture.md
.venv/bin/architecture-docs --check    # CI: fails if the file is stale
```

The diagrams are derived, never maintained by hand:

| Information | Source |
|---|---|
| routes, methods, deprecation | `app.openapi()` |
| version, owner, cache, contract fields | the registry |
| product → repository | AST of the loader (`repos.X()` calls) |
| repository → data source | AST of the `Repositories` container + adapter `source` |

`test_dokumentation_ist_aktuell` fails the build if you change the architecture
without regenerating. Run `architecture-docs` and commit the result.

---

## 15. Review checklist

For a pull request that adds or changes a data product:

- [ ] `owner` is set to a real team
- [ ] `transform()` is pure — no `repos`, no `Request`, no I/O
- [ ] tests for `transform()` cover empty input, `None` values and every filter
- [ ] `None` is preserved where the value is genuinely unknown (not coerced to `0`)
- [ ] version follows the MAJOR/MINOR rule — a changed **formula** is MAJOR
- [ ] the previous version is untouched, and marked `deprecated` + `sunset` if superseded
- [ ] `cache_ttl` is deliberate, and write endpoints invalidate the product
- [ ] no `HTTPException` outside `api/` and `products/router.py`
- [ ] no sample data added under `src/`
- [ ] `architecture-docs` was run and the result committed
- [ ] `pytest -q` is green

---

## 16. Pitfalls

**Blocking calls inside `async def`.** The synchronous Neo4j driver, `requests`,
`time.sleep` — any of these blocks the whole event loop, so *every* concurrent
request stalls, not just yours. Use the async drivers, or declare the endpoint
`def` (not `async def`) so FastAPI moves it to a thread pool. Never mix.

**One driver per request.** The driver/engine holds the connection pool and is
created once per process in the lifespan. Creating one per request throws the
pool away and adds a TCP+TLS handshake to every call.

**One session for the whole process.** Sessions are not thread-safe. This
produces sporadic, unreproducible failures under load.

**`from __future__ import annotations` in `products/router.py`.** That module
builds endpoint functions whose annotations are runtime objects from a closure.
The future import turns them into strings and FastAPI can no longer resolve them
— `TypeError` at startup. There is a comment in the file saying so; do not
"tidy it up".

**Reading routes from `app.routes`.** In the installed FastAPI version, included
routers appear as internal `_IncludedRouter` objects rather than being flattened.
Use `app.openapi()` — the public, stable contract.

**Forgetting `extra="forbid"` semantics.** `ProductParams` rejects unknown query
parameters, so `?stauts=Aktiv` returns 422 instead of silently returning
unfiltered data. If you add a filter, add it to the params model, not as a
free-form read of `request.query_params`.

**Editing a live version.** See [section 7](#7-recipe-release-a-breaking-change).
