"""
Builds real, TYPED FastAPI routes from the registry.

The obvious alternative would be a single generic route:

    @router.get("/data-products/{name}/{version}")
    async def get_product(name: str, version: str): ...

That works -- but it costs exactly what FastAPI is chosen for: the OpenAPI docs
would then say only "returns some JSON". No dashboard developer could look up
which fields a product returns under /docs, and no clients could be generated.

So at startup we create one route per (product, major) with its own
`response_model`. The result is complete OpenAPI documentation *and* "a new
product is just a new file".

IMPORTANT: this module deliberately has NO `from __future__ import annotations`.
The type annotations of the generated endpoints are runtime objects taken from
the closure (`ParamsModel`, `EnvelopeModel`). With the future import they become
strings, and FastAPI resolves those against the MODULE globals -- where a
closure variable does not exist.

And the failure is worse than a crash, so do not expect one to warn you. Adding
the import here (measured on fastapi 0.141) gives you:

    startup            fine, no error
    routes registered  all of them
    /docs              renders
    GET on any product 422 {"loc": ["query", "params"], "msg": "Field required"}

FastAPI stops seeing through `Annotated[ParamsModel, Query()]` and treats
`params` as a single query parameter literally named "params" instead of
unpacking the model's fields. Every data product answers 422 while everything
around it looks healthy. The test suite catches it (33 red), which is the only
reason this is a footnote rather than an outage.
"""

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from email.utils import format_datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel

from api.deps import SourcesDep
from core.errors import ForbiddenError
from core.security import CurrentPrincipal
from db.sources import Sources
from products.base import DataProduct, ProductEnvelope, ProductMeta, ProductParams
from products.cache import cache, etag_for
from products.registry import registry

log = logging.getLogger(__name__)


async def run_product(
    product: DataProduct, sources: Sources, params: ProductParams
) -> tuple[list[dict[str, Any]], int, str, str, dt.datetime]:
    """Runs a data product -- with caching. No HTTP involved, so it stays testable.

    Returns (rows, total, cache state, source, generation time).

    `rows` is the complete result for a normal product and just one page for a
    `paginated_by_source` one; `total` says how many rows matched either way, so
    the endpoint does not have to know which kind it is holding.

    Source, total and timestamp all go INTO the cache. Asking for them
    afterwards would make every cached response report `source="none"` (no query
    ran) and a `generated_at` of now instead of when the query actually ran --
    with cache_ttl=300 that is a five-minute error in the one field whose only
    job is to state how old the data is.
    """
    # The window belongs in the key exactly when it went into the query. Passing
    # the product's own flag makes the two impossible to get out of step.
    key = cache.make_key(
        product.name,
        product.major,
        params.cache_key(include_window=product.paginated_by_source),
    )
    cached = cache.get(key)
    if cached is not None:
        rows, total, source, generated_at = cached
        return rows, total, "hit", source, generated_at

    result = await product.loader(sources, params)
    # Two loader contracts, distinguished by the flag rather than by sniffing the
    # return value: a product that declares pagination but returns a bare list is
    # a mistake we want to see as an AttributeError here, not as a silently
    # wrong total_count in production.
    if product.paginated_by_source:
        rows, total = result.rows, result.total
    else:
        rows, total = result, len(result)

    generated_at = dt.datetime.now(dt.UTC)
    cache.set(key, (rows, total, sources.label, generated_at), product.cache_ttl)
    return rows, total, "miss" if product.cache_ttl else "bypass", sources.label, generated_at


def _make_endpoint(
    product: DataProduct,
) -> tuple[Callable[..., Awaitable[Any]], type[BaseModel]]:
    """Creates the endpoint function for exactly one data product."""
    ParamsModel = product.params_model
    EnvelopeModel = ProductEnvelope[product.item_model]

    async def endpoint(
        request: Request,
        response: Response,
        # `ParamsModel` is a VARIABLE holding a model class, and a type checker
        # cannot follow that -- it wants a type here, not a value. At runtime it
        # is exactly right, which is the whole trick of this module. There is no
        # way to express "the model this product happens to declare" statically,
        # so the check is silenced for this one line rather than worked around.
        params: Annotated[ParamsModel, Query()],  # pyright: ignore[reportInvalidTypeForm]
        sources: SourcesDep,
        principal: CurrentPrincipal,
    ) -> Any:  # noqa: ANN401 -- either the envelope dict or a bare 304 Response
        if not principal.may_access(product.required_groups):
            raise ForbiddenError(f"Access to '{product.name}' is not permitted.")

        rows, total, cache_state, source, generated_at = await run_product(
            product, sources, params
        )

        # THE double-slicing guard. For a paginated_by_source product the query
        # already applied SKIP/LIMIT, so slicing here would cut a window out of
        # a window: page 1 would look right and every later page would come back
        # empty. See ProductParams.limit for the full write-up.
        if product.paginated_by_source:
            page = rows
        else:
            page = rows[params.offset: params.offset + params.limit]

        payload = {
            "meta": ProductMeta(
                product=product.name,
                version=product.version,
                generated_at=generated_at,
                row_count=len(page),
                total_count=total,
                source=source,
                cache=cache_state,
                deprecated=product.deprecated,
                sunset=product.sunset,
            ).model_dump(mode="json"),
            "data": page,
        }

        # Conditional GET: unchanged -> 304 with no body.
        # `generated_at` stays out of the ETag; otherwise it would change on
        # every request and the ETag would be useless.
        tag = etag_for(payload["data"])
        response.headers["ETag"] = tag
        response.headers["Cache-Control"] = f"private, max-age={product.cache_ttl}"
        response.headers["X-Data-Product-Version"] = product.version
        if product.deprecated:
            # RFC 8594: clients can react to this, gateways can log it.
            response.headers["Deprecation"] = "true"
            if product.sunset:
                # RFC 9110 requires a fixed, English date format.
                # `strftime("%a, %d %b ...")` follows the container's locale and
                # produces "Do., 31 Dez. 2026" under LANG=de_DE -- unparseable.
                response.headers["Sunset"] = format_datetime(
                    dt.datetime.combine(product.sunset, dt.time.min, dt.UTC), usegmt=True
                )

        if request.headers.get("if-none-match") == tag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED,
                            headers=dict(response.headers))

        return payload

    endpoint.__name__ = f"get_{product.name.replace('-', '_')}_{product.path_version}"
    return endpoint, EnvelopeModel


def build_products_router() -> APIRouter:
    """One route per (product, major), plus a `latest` alias per product."""
    router = APIRouter(prefix="/data-products", tags=["Data products"])

    for product in registry.all():
        endpoint, envelope = _make_endpoint(product)
        marker = " [DEPRECATED]" if product.deprecated else ""
        router.add_api_route(
            f"/{product.name}/{product.path_version}",
            endpoint,
            methods=["GET"],
            response_model=envelope,
            response_model_exclude_none=False,
            summary=f"{product.summary}{marker}",
            description=(
                f"{product.description}\n\n"
                f"**Version:** {product.version} &nbsp;|&nbsp; "
                f"**Owner:** {product.owner} &nbsp;|&nbsp; "
                f"**Cache:** {product.cache_ttl}s"
            ),
            operation_id=f"{product.name.replace('-', '_')}_{product.path_version}",
            deprecated=product.deprecated,
            responses={304: {"description": "Not modified (the ETag matched)."}},
        )

    # `latest` is a convenience for exploration and notebooks.
    # Dashboards should ALWAYS request a fixed version -- otherwise a breaking
    # new major rolls into production unannounced.
    for name in registry.names():
        product = registry.latest(name)
        if product is None:
            continue
        endpoint, envelope = _make_endpoint(product)
        endpoint.__name__ = f"get_{name.replace('-', '_')}_latest"
        router.add_api_route(
            f"/{name}/latest",
            endpoint,
            methods=["GET"],
            response_model=envelope,
            summary=f"{product.summary} (currently {product.path_version})",
            description="Alias for the newest version. Do **not** use this from a "
                        "dashboard -- pin a fixed version there.",
            operation_id=f"{name.replace('-', '_')}_latest",
        )

    log.info("Data product routes created: %d products.", len(registry))
    return router
