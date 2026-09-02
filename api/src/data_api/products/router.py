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
the closure (`ParamsModel`, `EnvelopeModel`). With the future import they would
become strings and FastAPI could no longer resolve them -> TypeError at startup.
"""

import datetime as dt
import logging
from email.utils import format_datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status

from data_api.api.deps import SourcesDep
from data_api.core.errors import ForbiddenError
from data_api.core.security import CurrentPrincipal
from data_api.db.sources import Sources
from data_api.products.base import DataProduct, ProductEnvelope, ProductMeta
from data_api.products.cache import cache, etag_for
from data_api.products.registry import registry

log = logging.getLogger(__name__)


async def run_product(
    product: DataProduct, sources: Sources, params: Any
) -> tuple[list[Any], str, str, dt.datetime]:
    """Runs a data product -- with caching. No HTTP involved, so it stays testable.

    Returns (rows, cache state, source, generation time).

    Source and timestamp go INTO the cache. Asking for them afterwards would
    make every cached response report `source="none"` (no query ran) and a
    `generated_at` of now instead of when the query actually ran -- with
    cache_ttl=300 that is a five-minute error in the one field whose only job is
    to state how old the data is.
    """
    key = cache.make_key(product.name, product.major, params.cache_key())
    cached = cache.get(key)
    if cached is not None:
        rows, source, generated_at = cached
        return rows, "hit", source, generated_at

    rows = await product.loader(sources, params)
    generated_at = dt.datetime.now(dt.UTC)
    cache.set(key, (rows, sources.label, generated_at), product.cache_ttl)
    return rows, "miss" if product.cache_ttl else "bypass", sources.label, generated_at


def _make_endpoint(product: DataProduct):
    """Creates the endpoint function for exactly one data product."""
    ParamsModel = product.params_model
    EnvelopeModel = ProductEnvelope[product.item_model]

    async def endpoint(
        request: Request,
        response: Response,
        params: Annotated[ParamsModel, Query()],
        sources: SourcesDep,
        principal: CurrentPrincipal,
    ) -> Any:
        if not principal.may_access(product.required_groups):
            raise ForbiddenError(f"Access to '{product.name}' is not permitted.")

        rows, cache_state, source, generated_at = await run_product(product, sources, params)

        total = len(rows)
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
