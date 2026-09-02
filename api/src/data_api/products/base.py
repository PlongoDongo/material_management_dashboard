"""
Was ist ein Datenprodukt?

Ein Datenprodukt ist ein benannter, versionierter Datensatz mit einem Besitzer
-- nicht einfach "eine Route, die zufaellig die Datenbank abfragt".

Diese Datei enthaelt vier Dinge:

    ProductParams     Basis fuer die erlaubten Query-Parameter eines Produkts
    ProductMeta       die Metadaten, die jede Antwort mitliefert
    ProductEnvelope   das Antwortformat: {"meta": {...}, "data": [...]}
    DataProduct       die Beschreibung eines Produkts (Name, Version, Loader ...)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductParams(BaseModel):
    """Basis aller Parameter-Modelle. Jedes Produkt erbt davon.

    `extra="forbid"` heisst: ein unbekannter Query-Parameter ist ein Fehler.
    Schreibt ein Dashboard `?limmit=10`, gibt es 422 -- statt stillschweigend
    die ungefilterten Daten. Das ist der Unterschied zwischen "faellt im Test
    auf" und "faellt im Management-Meeting auf".
    """

    model_config = ConfigDict(extra="forbid")

    # limit/offset paginieren die FERTIGE Produktantwort und werden vom Router
    # angewandt -- NICHT in der Abfrage. Steht SKIP/LIMIT zusaetzlich im Cypher,
    # wird zweimal geschnitten und ab Seite 2 ist die Antwort leer.
    limit: int = Field(1000, ge=1, le=50_000, description="Maximale Zeilenzahl.")
    offset: int = Field(0, ge=0, description="Zeilen, die uebersprungen werden.")

    @field_validator("*", mode="after")
    @classmethod
    def _leere_liste_ist_kein_filter(cls, wert: object) -> object:
        """Eine leere Liste bedeutet "kein Filter", nicht "filtere auf nichts".

        Ohne diese Regel wird aus einem leeren Mehrfach-Auswahlfeld im Dashboard
        ein Filter, der alles wegwirft:

            Python:  status=[]
            Cypher:  WHERE $status IS NULL OR m.status IN $status
                     -> [] IS NULL ist false, x IN [] ist false
                     -> null Zeilen, ohne Fehler und ohne Hinweis

        Der Client raeumt leere Werte zwar heute schon weg, aber das ist eine
        Zusage des Aufrufers -- ein curl aus einem Notebook oder ein Dash-Callback,
        der direkt an httpx uebergibt, haette sie nicht. Deshalb hier, an EINER
        Stelle fuer alle Datenprodukte.

        Voraussetzung: Listenfilter werden als `list[X] | None` deklariert.
        """
        if isinstance(wert, list) and not wert:
            return None
        return wert

    def cache_key(self) -> str:
        """Die Parameter als Text -- Teil des Cache-Schluessels.

        `limit`/`offset` sind bewusst AUSGENOMMEN: gecacht wird das vollstaendige
        Ergebnis des Loaders, geschnitten wird erst danach. Stuenden sie im
        Schluessel, waere jede Seite ein kompletter Neulauf (bei supplier-risk
        zwei Datenbankabfragen plus Polars-Aggregation) und derselbe Datensatz
        laege N-mal im Cache. Sie waehlen einen Ausschnitt, sie bestimmen nicht
        den Datensatz.
        """
        return self.model_dump_json(exclude={"limit", "offset"})


class ProductMeta(BaseModel):
    """Steht in jeder Antwort unter "meta". Beantwortet: was, welche Version, wie alt?"""

    product: str
    version: str
    api_version: str = "v1"
    generated_at: dt.datetime
    row_count: int
    total_count: int | None = Field(None, description="Zeilen vor limit/offset.")
    source: str = Field("unknown", description="neo4j | postgres | Kombination.")
    cache: str = Field("miss", description="hit | miss | bypass")
    deprecated: bool = False
    sunset: dt.date | None = None


# --------------------------------------------------------------------------
# Das Antwortformat.
#
# Die naechsten drei Zeilen sind der einzige "fortgeschrittene" Teil dieser
# Datei. Sie sorgen dafuer, dass jedes Produkt in der API-Dokumentation unter
# /docs sein EIGENES Schema zeigt:
#
#     ProductEnvelope[MaterialRowV2]  ->  {"meta": {...}, "data": [MaterialRowV2]}
#     ProductEnvelope[SupplierRiskRow] ->  {"meta": {...}, "data": [SupplierRiskRow]}
#
# `TypeVar` ist der Platzhalter fuer "irgendein Zeilentyp", `Generic` sagt
# Pydantic, dass die Klasse mit einem Typ ausgefuellt werden kann. Man braucht
# das nur an dieser einen Stelle; beim Anlegen eines Datenprodukts kommt es
# nicht mehr vor.
# --------------------------------------------------------------------------
ItemT = TypeVar("ItemT")


class ProductEnvelope(BaseModel, Generic[ItemT]):
    """Umschlag um die Daten: `meta` + `data`.

    Warum ein Umschlag statt einer nackten Liste? Weil das Dashboard so erfaehrt,
    WELCHE Version es bekommen hat und wie alt die Daten sind. Und weil sich
    spaeter Metadaten ergaenzen lassen, ohne den Vertrag zu brechen -- bei einer
    nackten Liste waere schon der Wechsel zum Umschlag eine brechende Aenderung.
    """

    meta: ProductMeta
    data: list[ItemT]


@dataclass(frozen=True)
class DataProduct:
    """Die Beschreibung eines Datenprodukts.

    Wird in products/catalog/ angelegt und mit `registry.add(...)` veroeffentlicht.

    Zur Version: MAJOR.MINOR, z. B. "2.1".
      * Feld ergaenzt        -> MINOR hoch, gleiche Route  (bricht kein Dashboard)
      * Feld weg/umbenannt   -> MAJOR hoch, neue Route /v3
      * Bedeutung geaendert  -> MAJOR hoch (auch wenn das Schema gleich bleibt!)
    Im URL-Pfad steht nur das MAJOR, die volle Version in meta.version.
    """

    name: str                       # "material-overview"
    version: str                    # "2.1"
    summary: str                    # eine Zeile fuer die Doku
    item_model: type[BaseModel]     # das Zeilenschema = der Vertrag
    loader: Any                     # async def load(sources, params) -> list[dict]
    params_model: type[ProductParams] = ProductParams
    owner: str = "unassigned"       # wen fragt man bei Fragen?
    description: str = ""
    tags: tuple[str, ...] = ()
    cache_ttl: int = 60             # Sekunden; 0 = nicht cachen
    deprecated: bool = False
    sunset: dt.date | None = None   # ab wann diese Version abgeschaltet wird
    required_groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        teile = self.version.split(".")
        if len(teile) < 2 or not (teile[0].isdigit() and teile[1].isdigit()):
            raise ValueError(
                f"{self.name}: version muss 'MAJOR.MINOR' sein (z. B. '1.0'), "
                f"nicht {self.version!r}."
            )

    @property
    def major(self) -> int:
        """Die Hauptversionsnummer als Zahl: '2.1' -> 2."""
        return int(self.version.split(".")[0])

    @property
    def path_version(self) -> str:
        """Was im URL-Pfad steht: 'v2'."""
        return f"v{self.major}"
