"""
Tests der reinen Transformationen -- ohne HTTP, ohne Datenbank, ohne App.

Das ist die Testebene, die am schnellsten laeuft und die meisten echten Fehler
findet: hier steckt die Fachlichkeit. Die HTTP-Tests daneben pruefen nur noch,
dass die Verdrahtung stimmt.
"""
from __future__ import annotations

import datetime as dt

from data_api.products.catalog.material_overview_v2 import (
    MaterialParamsV2,
    transform as transform_material,
)
from data_api.products.catalog.supplier_risk_v1 import (
    SupplierRiskParams,
    transform as transform_risk,
)

ROHZEILEN = [
    {"material_nr": "MAT-1", "bezeichnung": "Schraube", "warengruppe": "Rohstoffe",
     "werk_id": "W-KOE", "werk_name": "Werk Koeln", "status": "Aktiv",
     "einheit": "ST", "bestand": 10, "preis": 2.5, "geaendert": "2026-01-01"},
    {"material_nr": "MAT-2", "bezeichnung": "Mutter", "warengruppe": "",
     "werk_id": "W-BER", "werk_name": "Werk Berlin", "status": "Gesperrt",
     "einheit": "ST", "bestand": None, "preis": 1.0, "geaendert": "2026-02-01"},
]


def test_bestandswert_wird_berechnet():
    zeilen = transform_material(ROHZEILEN, MaterialParamsV2())
    assert zeilen[0]["bestandswert"] == 25.0


def test_fehlender_bestand_ergibt_keinen_wert_statt_null():
    """Wichtig: None != 0. Ein fehlender Bestand ist unbekannt, nicht leer."""
    zeilen = transform_material(ROHZEILEN, MaterialParamsV2())
    assert zeilen[1]["bestand"] is None
    assert zeilen[1]["bestandswert"] is None


def test_leere_warengruppe_wird_zu_none_normalisiert():
    zeilen = transform_material(ROHZEILEN, MaterialParamsV2())
    assert zeilen[1]["warengruppe"] is None


def test_ohne_klassifizierung_findet_leere_und_fehlende_warengruppen():
    zeilen = transform_material(
        ROHZEILEN, MaterialParamsV2(ohne_klassifizierung=True)
    )
    assert [z["material_nr"] for z in zeilen] == ["MAT-2"]


def test_suche_ist_case_insensitiv_ueber_nummer_und_bezeichnung():
    assert len(transform_material(ROHZEILEN, MaterialParamsV2(suche="schraube"))) == 1
    assert len(transform_material(ROHZEILEN, MaterialParamsV2(suche="mat-"))) == 2


def test_min_bestandswert_filtert_zeilen_ohne_wert_aus():
    zeilen = transform_material(ROHZEILEN, MaterialParamsV2(min_bestandswert=10))
    assert [z["material_nr"] for z in zeilen] == ["MAT-1"]


# --- Risiko-Score -----------------------------------------------------------

STAMM = [{"lieferant_id": "L-1", "lieferant_name": "Puenktlich GmbH", "land": "DE",
          "anzahl_materialien": 5},
         {"lieferant_id": "L-2", "lieferant_name": "Spaet AG", "land": "AT",
          "anzahl_materialien": 3}]


def _lieferung(lid: str, verzug: int, reklamationen: int = 0) -> dict:
    zugesagt = dt.date(2026, 3, 1)
    return {"lieferant_id": lid, "material_nr": "MAT-1", "zugesagt_am": zugesagt,
            "geliefert_am": zugesagt + dt.timedelta(days=verzug),
            "menge": 100, "reklamationen": reklamationen}


def test_puenktlicher_lieferant_hat_score_null():
    zeilen = transform_risk(STAMM[:1], [_lieferung("L-1", 0)], SupplierRiskParams())
    assert zeilen[0]["risiko_score"] == 0.0
    assert zeilen[0]["liefertreue_pct"] == 100.0
    assert zeilen[0]["risiko_klasse"] == "niedrig"


def test_verzug_und_reklamationen_erhoehen_den_score():
    lieferungen = [_lieferung("L-1", 0), _lieferung("L-2", 14, reklamationen=1)]
    zeilen = transform_risk(STAMM, lieferungen, SupplierRiskParams())
    nach_id = {z["lieferant_id"]: z for z in zeilen}
    assert nach_id["L-2"]["risiko_score"] > nach_id["L-1"]["risiko_score"]
    # 0.5*100 + 0.3*100 + 0.2*100 = 100 bei maximalem Verzug
    assert nach_id["L-2"]["risiko_score"] == 100.0
    assert nach_id["L-2"]["risiko_klasse"] == "hoch"


def test_toleranz_tage_verschieben_die_puenktlichkeitsgrenze():
    lieferungen = [_lieferung("L-1", 2)]
    ohne = transform_risk(STAMM[:1], lieferungen, SupplierRiskParams(toleranz_tage=0))
    mit = transform_risk(STAMM[:1], lieferungen, SupplierRiskParams(toleranz_tage=3))
    assert ohne[0]["liefertreue_pct"] == 0.0
    assert mit[0]["liefertreue_pct"] == 100.0
    assert mit[0]["risiko_score"] < ohne[0]["risiko_score"]


def test_lieferant_ohne_lieferungen_wird_ausgeblendet():
    zeilen = transform_risk(STAMM, [_lieferung("L-1", 0)],
                            SupplierRiskParams(min_lieferungen=1))
    assert [z["lieferant_id"] for z in zeilen] == ["L-1"]


def test_leere_eingaben_ergeben_leere_ausgabe_statt_absturz():
    assert transform_risk([], [], SupplierRiskParams()) == []
    assert transform_risk(STAMM, [], SupplierRiskParams(min_lieferungen=0)) != []
