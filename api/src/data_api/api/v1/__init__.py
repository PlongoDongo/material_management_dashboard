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

from data_api.api.v1 import catalog, health, mappings
from data_api.products.router import build_products_router


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    router.include_router(health.router)
    router.include_router(catalog.router)
    router.include_router(build_products_router())   # generated from the registry
    router.include_router(mappings.router)           # hand-written (write side)
    return router
