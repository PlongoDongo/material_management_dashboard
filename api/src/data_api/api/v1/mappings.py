"""
Die Schreibseite -- handgeschriebene Endpunkte, bewusst NICHT ueber die Registry.

Warum die Trennung? Weil Lesen und Schreiben unterschiedliche Vertraege haben:

    Datenprodukt (GET)   ein Vertrag ueber die FORM der Daten. Cachebar,
                         idempotent, versioniert, generierbar.
    Kommando (POST/PATCH/DELETE)
                         ein Vertrag ueber eine AKTION. Hat Vorbedingungen,
                         Nebenwirkungen, Berechtigungen, Transaktionen und
                         invalidiert Caches.

Das ist "CQRS-lite": ein generischer Codegenerator kann das Zweite nicht
sinnvoll erzeugen, und der Versuch macht die Abstraktion kaputt. Schreibende
Endpunkte gehoeren deshalb in normale, von Hand geschriebene Router unter
/api/v1/<thema>.

Zu den HTTP-Methoden (die Frage kam auf: "POST, PUT oder UPDATE"):
    POST    neu anlegen, oder eine Aktion ausloesen         nicht idempotent
    PUT     vollstaendig ersetzen (der ganze Datensatz)     idempotent
    PATCH   teilweise aendern (nur die gesendeten Felder)   idempotent
    DELETE  loeschen                                        idempotent
Ein "UPDATE" gibt es in HTTP nicht -- gemeint ist meist PATCH.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Body, status
from pydantic import BaseModel, Field

from data_api.api.deps import SourcesDep
from data_api.core.security import CurrentPrincipal
from data_api.products.cache import cache

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mappings", tags=["Mappings (Schreiben)"])


class MappingIn(BaseModel):
    """Eingabemodell. Getrennt vom Ausgabemodell -- immer.

    Der Client darf `id` und `geaendert_am` nicht setzen; stuenden sie in einem
    gemeinsamen Modell, muesste man sie muehsam wegvalidieren. Zwei kleine
    Modelle sind einfacher als ein grosses mit Ausnahmen.
    """

    material_nr: str = Field(min_length=1, max_length=40)
    ziel_warengruppe: str = Field(min_length=1, max_length=80)
    kommentar: str | None = Field(None, max_length=500)


class MappingOut(MappingIn):
    id: str
    geaendert_am: dt.datetime
    geaendert_von: str


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Neues Mapping anlegen",
    responses={409: {"description": "Mapping existiert bereits."}},
)
async def create_mapping(
    payload: Annotated[MappingIn, Body()],
    sources: SourcesDep,
    principal: CurrentPrincipal,
) -> MappingOut:
    """Legt ein Material-zu-Warengruppe-Mapping an.

    Der eigentliche Schreibvorgang gehoert in ein Repository (hier noch nicht
    implementiert, weil die Zieltabelle fehlt). Wichtig ist das Muster darum
    herum: nach jedem Schreiben werden die betroffenen Datenprodukte aus dem
    Cache geworfen -- sonst zeigt das Dashboard bis zu `cache_ttl` Sekunden
    lang den alten Stand und der Nutzer glaubt, das Speichern habe nicht
    funktioniert.
    """
    # TODO(datenquelle): await sources.postgres(INSERT_SQL, ...)
    log.info("Mapping angelegt von %s: %s -> %s",
             principal.subject, payload.material_nr, payload.ziel_warengruppe)

    invalidated = cache.invalidate("material-overview")
    log.info("Cache invalidiert: %d Eintraege.", invalidated)

    return MappingOut(
        **payload.model_dump(),
        id=f"map-{payload.material_nr}",
        geaendert_am=dt.datetime.now(dt.UTC),
        geaendert_von=principal.subject,
    )


@router.patch("/{mapping_id}", summary="Mapping teilweise aendern")
async def patch_mapping(
    mapping_id: str,
    payload: Annotated[MappingIn, Body()],
    sources: SourcesDep,
    principal: CurrentPrincipal,
) -> MappingOut:
    # TODO(datenquelle): analog zu create_mapping.
    cache.invalidate("material-overview")
    return MappingOut(
        **payload.model_dump(),
        id=mapping_id,
        geaendert_am=dt.datetime.now(dt.UTC),
        geaendert_von=principal.subject,
    )
