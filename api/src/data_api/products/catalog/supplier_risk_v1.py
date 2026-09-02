"""
supplier-risk v1 -- zwei Quellen, echte Berechnung.

Dieses Produkt zeigt, wofuer der API-Layer da ist. Es verbindet
    Stammdaten aus Neo4j     (welcher Lieferant liefert wie viele Materialien)
mit Bewegungsdaten aus Postgres (Liefertreue, Reklamationen)
und berechnet daraus einen Risiko-Score.

Wuerde man das im Dashboard machen, muesste jedes Dashboard beide Verbindungen
kennen, beide Zugangsdaten halten und die Score-Formel kopieren. Beim ersten
Formelwechsel zeigen dann zwei Dashboards zwei verschiedene Zahlen -- und
niemand weiss, welche stimmt.

Die Berechnung laeuft in Polars: dieselbe Bibliothek, die die Dashboards schon
benutzen, spaltenorientiert und deutlich schneller als Schleifen ueber dicts.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from data_api.db.sources import Sources
from data_api.products.base import DataProduct, ProductParams
from data_api.products.registry import registry

# 1a. Stammdaten aus dem Graphen.
#
# `$land` ist ein PARAMETER, kein eingesetzter Text. Die Zeile
#     WHERE $land IS NULL OR s.land IN $land
# ist die Standardredewendung fuer einen OPTIONALEN Filter: wird nichts
# uebergeben, faellt die Bedingung weg; wird etwas uebergeben, greift sie.
# So braucht man nicht zwei Abfragen oder zusammengebauten Text.
#
# Parametrisierbar sind Werte, Listen sowie SKIP und LIMIT. NICHT
# parametrisierbar sind Labels, Beziehungstypen und Property-Namen -- die
# gehoeren zur Struktur der Abfrage.
CYPHER = """
MATCH (s:Lieferant)-[:SUPPLIES]->(m:Material)
WHERE $land IS NULL OR s.land IN $land
RETURN s.id     AS lieferant_id,
       s.name   AS lieferant_name,
       s.land   AS land,
       count(m) AS anzahl_materialien
ORDER BY s.id
"""

# 1b. Bewegungsdaten aus Postgres. `:seit` wird als Parameter uebergeben,
#     nicht in den Text eingesetzt (SQL-Injection).
SQL = """
SELECT lieferant_id, material_nr, geliefert_am, zugesagt_am, menge, reklamationen
FROM   lieferungen
WHERE  geliefert_am >= :seit
ORDER  BY lieferant_id, geliefert_am
"""


# 2. Der Vertrag.
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


# 3. Die erlaubten Filter.
class SupplierRiskParams(ProductParams):
    seit: dt.date = Field(dt.date(2026, 1, 1), description="Beginn des Auswertungszeitraums.")
    toleranz_tage: int = Field(
        0, ge=0, le=30, description="Verzug bis einschliesslich X Tagen gilt als puenktlich."
    )
    min_lieferungen: int = Field(
        1, ge=0, description="Lieferanten mit weniger Lieferungen werden ausgeblendet."
    )
    risiko_klasse: list[str] | None = None
    # Dieser Filter wird an die Datenbank durchgereicht (siehe CYPHER oben),
    # nicht erst hinterher in Python angewandt -- er verkleinert das Ergebnis
    # schon im Graphen. Neu in 1.1: ein ERGAENZTER optionaler Parameter ist
    # abwaertskompatibel, also MINOR und dieselbe Route /v1.
    land: list[str] | None = Field(None, description="Nur Lieferanten aus diesen Laendern.")


# Gewichte der Score-Formel. Bewusst als benannte Konstanten und nicht im Code
# verstreut: das ist die fachliche Stellschraube, ueber die diskutiert wird.
# Aendert sich eine davon, aendert sich die BEDEUTUNG von risiko_score -> das
# ist eine brechende Aenderung und braucht eine neue Hauptversion.
GEWICHT_VERZUG = 0.5
GEWICHT_TREUE = 0.3
GEWICHT_REKLAMATION = 0.2


def _risiko_klasse(score: float) -> str:
    if score >= 60:
        return "hoch"
    if score >= 30:
        return "mittel"
    return "niedrig"


# 4. Die Fachlichkeit -- rein, ohne Datenbank, ohne HTTP.
def transform(
    lieferanten: list[dict[str, Any]],
    lieferungen: list[dict[str, Any]],
    params: SupplierRiskParams,
) -> list[dict[str, Any]]:
    if not lieferanten:
        return []

    stamm = pl.DataFrame(
        lieferanten,
        schema={"lieferant_id": pl.Utf8, "lieferant_name": pl.Utf8,
                "land": pl.Utf8, "anzahl_materialien": pl.Int64},
    )

    if lieferungen:
        kennzahlen = (
            pl.DataFrame(lieferungen)
            .with_columns(verzug_tage=(pl.col("geliefert_am") - pl.col("zugesagt_am")).dt.total_days())
            .group_by("lieferant_id")
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
            # Drei normierte Anteile. Verzug wird bei 14 Tagen gekappt --
            # darueber ist "sehr schlecht" nicht mehr sinnvoll differenzierbar.
            risiko_score=(
                GEWICHT_VERZUG * (pl.col("mittlerer_verzug_tage").clip(0, 14) / 14 * 100)
                + GEWICHT_TREUE * ((1 - pl.col("puenktlich")) * 100)
                + GEWICHT_REKLAMATION * (pl.col("reklamationsquote") * 100)
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

    zeilen = df.to_dicts()
    for zeile in zeilen:
        zeile.pop("puenktlich", None)
        zeile.pop("reklamationsquote", None)
        zeile["risiko_klasse"] = _risiko_klasse(zeile["risiko_score"])

    if params.risiko_klasse:
        zeilen = [z for z in zeilen if z["risiko_klasse"] in params.risiko_klasse]
    return zeilen


# 5. Die Verdrahtung -- hier sieht man, dass beide Quellen benutzt werden.
async def load(sources: Sources, params: SupplierRiskParams) -> list[dict[str, Any]]:
    """Verknuepft Lieferantenstammdaten mit der Lieferhistorie und bewertet das Risiko."""
    # Parameter werden als benannte Werte uebergeben. Sie NIE in den
    # Abfragetext einsetzen -- das waere eine Injection-Luecke und verhindert,
    # dass die Datenbank den Abfrageplan wiederverwendet.
    lieferanten = await sources.neo4j(CYPHER, land=params.land)
    lieferungen = await sources.postgres(SQL, seit=params.seit)
    return transform(lieferanten, lieferungen, params)


# 6. Veroeffentlichen.
registry.add(DataProduct(
    name="supplier-risk",
    version="1.1",
    summary="Lieferantenrisiko aus Stammdaten (Neo4j) und Liefertreue (Postgres)",
    item_model=SupplierRiskRow,
    params_model=SupplierRiskParams,
    loader=load,
    owner="team-supply-chain",
    tags=("lieferant", "risiko", "cross-source"),
    cache_ttl=300,   # teure Berechnung, Daten aendern sich taeglich
))
