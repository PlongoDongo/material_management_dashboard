"""
Was ist ein "Datenprodukt"?

Ein Datenprodukt ist NICHT "eine Route, die zufaellig die DB abfragt". Es ist
ein benannter, versionierter Vertrag mit einem Besitzer:

    name        material-overview       stabiler fachlicher Name
    version     2.1                     MAJOR.MINOR (siehe unten)
    item_model  Pydantic-Modell         DER Vertrag: Felder, Typen, Pflicht/Optional
    params_model                        erlaubte Query-Parameter (typisiert)
    loader      async (repos, params)   Query + Transformation
    owner       "team-materials"        wen fragt man bei Fragen
    cache_ttl   60                      wie frisch muss es sein

Versionsregel (die wichtigste Konvention im ganzen Konzept):

    MAJOR hoch  = brechende Aenderung (Feld entfernt/umbenannt/Typ geaendert)
                  -> NEUE Route: /data-products/material-overview/v2
                  -> v1 bleibt bestehen, bis alle Dashboards migriert sind
    MINOR hoch  = abwaertskompatibel (Feld ergaenzt, Doku, Performance)
                  -> GLEICHE Route, nur die Metadaten melden 2.1 statt 2.0

Deshalb steht im Pfad nur das Major (`v2`) und in `meta.version` das volle
`2.1`. Ein Dashboard, das gegen v2 gebaut ist, bricht nie durch einen Minor.

Der `loader` bekommt ausschliesslich `Repositories` und die geparsten
Parameter -- kein Request, keine Response, kein FastAPI. Dadurch ist jedes
Datenprodukt ohne Webserver testbar.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from data_api.db.repositories import Repositories


class ProductParams(BaseModel):
    """Basis aller Query-Parameter-Modelle.

    `extra="forbid"`: ein Tippfehler im Dashboard (`?limmit=10`) liefert 422
    statt stillschweigend die ungefilterten Daten. Das ist der Unterschied
    zwischen "faellt im Test auf" und "faellt im Management-Meeting auf".
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(1000, ge=1, le=50_000, description="Maximale Zeilenzahl.")
    offset: int = Field(0, ge=0, description="Zeilen, die uebersprungen werden.")

    def cache_key(self) -> str:
        return self.model_dump_json()


ItemT = TypeVar("ItemT")


class ProductMeta(BaseModel):
    """Der Umschlag um die Daten. Beantwortet: was, welche Version, wie alt?"""

    product: str
    version: str
    api_version: str = "v1"
    generated_at: dt.datetime
    row_count: int
    total_count: int | None = Field(
        None, description="Zeilen vor limit/offset -- fuer Paginierung im Dashboard."
    )
    source: str = Field("unknown", description="neo4j | postgres | Kombination.")
    cache: str = Field("miss", description="hit | miss | bypass")
    deprecated: bool = False
    sunset: dt.date | None = None


class ProductEnvelope(BaseModel, Generic[ItemT]):
    """Antwortformat ALLER Datenprodukte.

    Warum ein Umschlag statt einer nackten Liste?
      * Das Dashboard erfaehrt, WELCHE Version es bekommen hat (Debugging).
      * `generated_at` erlaubt "Stand: 10:42" in der UI.
      * `total_count` ermoeglicht serverseitiges Paging.
      * Zusaetzliche Metadaten spaeter sind KEINE brechende Aenderung -- bei
        einer nackten Liste waeren sie es.
    """

    meta: ProductMeta
    data: list[ItemT]


# Der Loader: Repositories + Parameter rein, Zeilen raus. Mehr Kopplung nicht.
Loader = Callable[[Repositories, Any], Awaitable[list[Any]]]


@dataclass(frozen=True, slots=True)
class DataProduct:
    name: str
    version: str
    summary: str
    item_model: type[BaseModel]
    loader: Loader
    params_model: type[ProductParams] = ProductParams
    owner: str = "unassigned"
    description: str = ""
    tags: tuple[str, ...] = ()
    cache_ttl: int = 60                      # Sekunden; 0 = nicht cachen
    deprecated: bool = False
    sunset: dt.date | None = None            # ab wann v_x abgeschaltet wird
    required_groups: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        parts = self.version.split(".")
        if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
            raise ValueError(
                f"{self.name}: version muss 'MAJOR.MINOR' sein (z. B. '1.0'), "
                f"nicht {self.version!r}."
            )

    @property
    def major(self) -> int:
        return int(self.version.split(".")[0])

    @property
    def path_version(self) -> str:
        """Was im URL-Pfad steht: v1, v2, ... (nur Major -- siehe Modul-Docstring)."""
        return f"v{self.major}"

    @property
    def key(self) -> tuple[str, int]:
        return (self.name, self.major)
