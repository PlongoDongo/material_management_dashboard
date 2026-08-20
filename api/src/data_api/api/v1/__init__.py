"""
Zusammenbau der API-Version v1.

Router-Management in FastAPI ist bewusst simpel: jedes fachliche Thema bekommt
einen `APIRouter`, und genau eine Stelle steckt sie zusammen. Kein Router
importiert einen anderen, keiner kennt die App -- deshalb gibt es hier nie
Zirkelimporte, egal wie viele Router dazukommen.

Die API-Version (v1) steht im Prefix und ist NICHT dasselbe wie die
Datenprodukt-Version:

    /api/v1/data-products/material-overview/v2
     ^^^^^^                                 ^^
     Transportvertrag                       Datenvertrag
     (Fehlerformat, Auth, Umschlag)         (Felder dieser einen Tabelle)

Beide aendern sich unabhaengig voneinander -- und genau darum haben sie
getrennte Versionsnummern. Ein neues Feld in einem Datenprodukt darf nicht die
ganze API auf v2 heben, und ein geaenderter Auth-Mechanismus darf nicht jedes
Datenprodukt neu versionieren.
"""
from fastapi import APIRouter

from data_api.api.v1 import catalog, health, mappings
from data_api.products.router import build_products_router


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    router.include_router(health.router)
    router.include_router(catalog.router)
    router.include_router(build_products_router())   # generiert aus der Registry
    router.include_router(mappings.router)           # handgeschrieben (Schreibseite)
    return router
