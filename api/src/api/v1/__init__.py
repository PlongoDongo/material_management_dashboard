"""
Assembly of API version v1.

Router management in FastAPI is deliberately simple: every topic gets its own
`APIRouter`, and exactly one place wires them together. No router imports
another, none of them knows the app -- which is why circular imports never
appear here, no matter how many routers are added.

The API version (v1) lives in the prefix and is NOT the same as a data product
version:

    /api/v1/data-products/material-overview/v3
     ^^^^^^                                 ^^
     transport contract                     data contract
     (error format, auth, envelope)         (fields of this one dataset)

They change independently -- which is exactly why they have separate version
numbers. A new field in one data product must not push the whole API to v2, and
a changed auth mechanism must not re-version every data product.
"""
from fastapi import APIRouter

from api.v1 import catalog, health, relationships, relationships_orm
from products.router import build_products_router

API_V1_PREFIX = "/api/v1"

# The hand-written routers, in the order they are mounted. Exposed as a tuple
# because `include_router` swallows them: once mounted, FastAPI keeps a router
# as a private `_IncludedRouter` and its `APIRoute` objects are no longer
# reachable from the app. Their dependencies are, though, and
# tests/test_write_routes.py reads those -- which roles a write route demands
# and which products it invalidates.
#
# The generated data product router is deliberately NOT in here: it has no
# hand-written dependencies to inspect, and the registry already describes it.
TOPIC_ROUTERS = (health.router, catalog.router, relationships.router,
                 relationships_orm.router)


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix=API_V1_PREFIX)
    router.include_router(health.router)
    router.include_router(catalog.router)
    router.include_router(build_products_router())   # generated from the registry
    router.include_router(relationships.router)      # hand-written (write side)
    router.include_router(relationships_orm.router)  # the same, via the ORM
    return router
