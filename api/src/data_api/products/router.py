"""
Baut aus der Registry echte, TYPISIERTE FastAPI-Routen.

Die naheliegende Alternative waere eine einzige generische Route:

    @router.get("/data-products/{name}/{version}")
    async def get_product(name: str, version: str): ...

Die funktioniert -- kostet aber genau das, wofuer man FastAPI nimmt: In der
OpenAPI-Doku stuende dann nur "gibt irgendein JSON zurueck". Kein Dashboard-
Entwickler koennte unter /docs nachsehen, welche Felder ein Produkt liefert,
und es gaebe keine generierbaren Clients.

Darum erzeugen wir beim Start pro (Produkt, Major) eine eigene Route mit
eigenem `response_model`. Ergebnis: /docs zeigt jedes Datenprodukt mit
vollstaendigem Schema, und trotzdem ist ein neues Produkt nur eine neue Datei.

WICHTIG: In diesem Modul steht bewusst KEIN `from __future__ import annotations`.
Die Typannotationen der erzeugten Endpunkte sind Laufzeitobjekte aus der
Closure (`ParamsModel`, `EnvelopeModel`). Mit der Future-Zeile wuerden sie zu
Strings, und FastAPI koennte sie nicht mehr aufloesen -> TypeError beim Start.
"""

import datetime as dt
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from data_api.api.deps import ReposDep
from data_api.core.security import CurrentPrincipal
from data_api.db.repositories import Repositories
from data_api.products.base import DataProduct, ProductEnvelope, ProductMeta
from data_api.products.cache import cache, etag_for
from data_api.products.registry import registry

log = logging.getLogger(__name__)


async def run_product(
    product: DataProduct, repos: Repositories, params: Any
) -> tuple[list[Any], str]:
    """Fuehrt ein Datenprodukt aus -- mit Cache. Ohne HTTP, damit testbar.

    Rueckgabe: (Zeilen, cache-Status). Der Cache haelt bereits serialisierte
    dicts, keine Pydantic-Objekte: das spart bei grossen Tabellen die
    Re-Validierung und ist der Grund, warum `response_model` unten mit
    `model_construct`-artigen Rohdaten umgehen kann.
    """
    key = cache.make_key(product.name, product.major, params.cache_key())
    cached = cache.get(key)
    if cached is not None:
        return cached, "hit"

    rows = await product.loader(repos, params)
    cache.set(key, rows, product.cache_ttl)
    return rows, "miss" if product.cache_ttl else "bypass"


def _make_endpoint(product: DataProduct):
    """Erzeugt die Endpunktfunktion fuer genau ein Datenprodukt."""
    ParamsModel = product.params_model
    EnvelopeModel = ProductEnvelope[product.item_model]

    async def endpoint(
        request: Request,
        response: Response,
        params: Annotated[ParamsModel, Query()],
        repos: ReposDep,
        principal: CurrentPrincipal,
    ) -> Any:
        if not principal.has_any(product.required_groups):
            from fastapi import HTTPException

            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail=f"Zugriff auf '{product.name}' nicht erlaubt.")

        rows, cache_state = await run_product(product, repos, params)

        total = len(rows)
        page = rows[params.offset: params.offset + params.limit]

        payload = {
            "meta": ProductMeta(
                product=product.name,
                version=product.version,
                generated_at=dt.datetime.now(dt.UTC),
                row_count=len(page),
                total_count=total,
                source=repos.source_label,
                cache=cache_state,
                deprecated=product.deprecated,
                sunset=product.sunset,
            ).model_dump(mode="json"),
            "data": page,
        }

        # Konditionales GET: unveraendert -> 304 ohne Body.
        # `generated_at` bleibt aus dem ETag heraus, sonst aendert es sich bei
        # jedem Request und der Cache waere wirkungslos.
        tag = etag_for(payload["data"])
        response.headers["ETag"] = tag
        response.headers["Cache-Control"] = f"private, max-age={product.cache_ttl}"
        response.headers["X-Data-Product-Version"] = product.version
        if product.deprecated:
            # RFC 8594: Clients koennen darauf reagieren, Gateways loggen es.
            response.headers["Deprecation"] = "true"
            if product.sunset:
                response.headers["Sunset"] = product.sunset.strftime("%a, %d %b %Y 00:00:00 GMT")

        if request.headers.get("if-none-match") == tag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED,
                            headers=dict(response.headers))

        return payload

    endpoint.__name__ = f"get_{product.name.replace('-', '_')}_{product.path_version}"
    return endpoint, EnvelopeModel


def build_products_router() -> APIRouter:
    """Eine Route je (Produkt, Major) plus je einen `latest`-Alias."""
    router = APIRouter(prefix="/data-products", tags=["Datenprodukte"])

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
            responses={304: {"description": "Nicht geaendert (ETag passt)."}},
        )

    # `latest` ist Komfort fuer Exploration und Notebooks.
    # Dashboards sollten IMMER eine feste Version anfragen -- sonst wandert ein
    # brechendes v2 unangekuendigt in die Produktion.
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
            summary=f"{product.summary} (aktuell {product.path_version})",
            description="Alias auf die neueste Version. **Nicht** fuer Dashboards "
                        "verwenden -- dort immer eine feste Version anfragen.",
            operation_id=f"{name.replace('-', '_')}_latest",
        )

    log.info("Datenprodukt-Routen erzeugt: %d Produkte.", len(registry))
    return router
