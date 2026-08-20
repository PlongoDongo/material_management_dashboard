"""
supplier-risk v1 -- der interessante Fall: zwei Quellen, echte Berechnung.

Dieses Produkt zeigt, wofuer ein API-Layer eigentlich da ist. Es verbindet
    Stammdaten aus Neo4j   (welcher Lieferant liefert wie viele Materialien)
mit Bewegungsdaten aus Postgres (Liefertreue, Reklamationen)
und berechnet daraus einen Risiko-Score.

Wuerde man das im Dashboard machen, muesste jedes Dashboard beide Verbindungen
kennen, beide Zugangsdaten halten und die Score-Formel kopieren. Beim ersten
Formelwechsel zeigen dann zwei Dashboards zwei verschiedene Zahlen -- und
niemand weiss, welche stimmt.

Die Berechnung laeuft in Polars: dieselbe Bibliothek, die die Dashboards schon
benutzen, spaltenorientiert und deutlich schneller als Schleifen ueber dicts,
sobald es um mehr als ein paar Tausend Zeilen geht.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from data_api.db.repositories import Repositories
from data_api.products.base import ProductParams
from data_api.products.registry import data_product


class SupplierRiskRow(BaseModel):
    lieferant_id: str
    lieferant_name: str | None = None
    land: str | None = None
    anzahl_materialien: int = 0
    lieferungen: int = Field(0, description="Beruecksichtigte Lieferungen im Zeitfenster.")
    liefertreue_pct: float = Field(0.0, description="Anteil puenktlicher Lieferungen in %.")
    mittlerer_verzug_tage: float = 0.0
    reklamationsquote_pct: float = 0.0
    risiko_score: float = Field(0.0, description="0 (unkritisch) bis 100 (kritisch).")
    risiko_klasse: str = Field("niedrig", description="niedrig | mittel | hoch")


class SupplierRiskParams(ProductParams):
    seit: dt.date = Field(
        dt.date(2026, 1, 1), description="Beginn des Auswertungszeitraums."
    )
    toleranz_tage: int = Field(
        0, ge=0, le=30, description="Verzug bis einschliesslich X Tagen gilt als puenktlich."
    )
    min_lieferungen: int = Field(
        1, ge=0, description="Lieferanten mit weniger Lieferungen werden ausgeblendet."
    )
    risiko_klasse: list[str] | None = None


# Gewichte der Score-Formel. Bewusst hier als Konstanten und nicht im Code
# verstreut: das ist die fachliche Stellschraube, ueber die diskutiert wird.
_W_VERZUG = 0.5
_W_TREUE = 0.3
_W_REKLAMATION = 0.2


def _risiko_klasse(score: float) -> str:
    if score >= 60:
        return "hoch"
    if score >= 30:
        return "mittel"
    return "niedrig"


def transform(
    suppliers: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    params: SupplierRiskParams,
) -> list[dict[str, Any]]:
    """Reine Funktion -- der gesamte fachliche Kern, ohne DB und ohne HTTP.

    Genau hier liegen die Tests (tests/test_transformations.py). Die Formel zu
    aendern heisst: diese Funktion und ihre Tests anfassen, sonst nichts.
    """
    if not suppliers:
        return []

    stamm = pl.DataFrame(
        suppliers,
        schema={"lieferant_id": pl.Utf8, "lieferant_name": pl.Utf8,
                "land": pl.Utf8, "anzahl_materialien": pl.Int64},
    )

    if deliveries:
        lief = pl.DataFrame(deliveries).with_columns(
            verzug_tage=(pl.col("geliefert_am") - pl.col("zugesagt_am")).dt.total_days(),
        )
        kennzahlen = (
            lief.group_by("lieferant_id")
            .agg(
                lieferungen=pl.len(),
                mittlerer_verzug_tage=pl.col("verzug_tage").mean(),
                puenktlich=(pl.col("verzug_tage") <= params.toleranz_tage).mean(),
                reklamationsquote=(pl.col("reklamationen") > 0).mean(),
            )
        )
    else:
        kennzahlen = pl.DataFrame(
            schema={"lieferant_id": pl.Utf8, "lieferungen": pl.UInt32,
                    "mittlerer_verzug_tage": pl.Float64, "puenktlich": pl.Float64,
                    "reklamationsquote": pl.Float64},
        )

    df = (
        stamm.join(kennzahlen, on="lieferant_id", how="left")
        .with_columns(
            lieferungen=pl.col("lieferungen").fill_null(0).cast(pl.Int64),
            mittlerer_verzug_tage=pl.col("mittlerer_verzug_tage").fill_null(0.0),
            puenktlich=pl.col("puenktlich").fill_null(1.0),
            reklamationsquote=pl.col("reklamationsquote").fill_null(0.0),
        )
        .with_columns(
            # Score aus drei normierten Anteilen. Verzug wird bei 14 Tagen
            # gekappt -- darueber ist "sehr schlecht" nicht mehr differenziert.
            risiko_score=(
                _W_VERZUG * (pl.col("mittlerer_verzug_tage").clip(0, 14) / 14 * 100)
                + _W_TREUE * ((1 - pl.col("puenktlich")) * 100)
                + _W_REKLAMATION * (pl.col("reklamationsquote") * 100)
            ).round(1)
        )
        .with_columns(
            liefertreue_pct=(pl.col("puenktlich") * 100).round(1),
            reklamationsquote_pct=(pl.col("reklamationsquote") * 100).round(1),
            mittlerer_verzug_tage=pl.col("mittlerer_verzug_tage").round(2),
        )
        .filter(pl.col("lieferungen") >= params.min_lieferungen)
        .sort("risiko_score", descending=True)
    )

    rows = df.to_dicts()
    for row in rows:
        row.pop("puenktlich", None)
        row.pop("reklamationsquote", None)
        row["risiko_klasse"] = _risiko_klasse(row["risiko_score"])

    if params.risiko_klasse:
        rows = [r for r in rows if r["risiko_klasse"] in params.risiko_klasse]
    return rows


@data_product(
    name="supplier-risk",
    version="1.0",
    summary="Lieferantenrisiko aus Stammdaten (Neo4j) und Liefertreue (Postgres)",
    item_model=SupplierRiskRow,
    params_model=SupplierRiskParams,
    owner="team-supply-chain",
    tags=("lieferant", "risiko", "cross-source"),
    cache_ttl=300,   # teure Berechnung, Daten aendern sich taeglich -> 5 Minuten
)
async def load(repos: Repositories, params: SupplierRiskParams) -> list[dict[str, Any]]:
    """Verknuepft Lieferantenstammdaten mit der Lieferhistorie und bewertet das Risiko."""
    materials_repo = await repos.materials()
    deliveries_repo = await repos.deliveries()

    suppliers = await materials_repo.fetch_suppliers()
    deliveries = await deliveries_repo.fetch_deliveries(params.seit)
    return transform(suppliers, deliveries, params)
