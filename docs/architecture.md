# Architektur (automatisch erzeugt)

> Diese Datei wird von `python -m data_api.architecture` aus der laufenden
> App erzeugt. **Nicht von Hand bearbeiten** -- Aenderungen gehen beim
> naechsten Lauf verloren. Das Konzept dahinter steht in
> [`api_layer_concept.md`](api_layer_concept.md).

Stand: 2026-08-20 · 3 Datenprodukte · 9 Routen

## Datenfluss

Von der Route bis zur Datenquelle. ⚠ markiert auslaufende Versionen.

```mermaid
flowchart LR
  subgraph clients["Konsumenten"]
    dash["Dash-Dashboards"]
  end

  subgraph routes["Routen /api/v1"]
    r__api_v1_catalog["GET /catalog"]
    r__api_v1_catalog__name_["GET /catalog/{name}"]
    r__api_v1_data_products_material_overview_v1["GET /data-products/material-overview/v1 ⚠"]
    r__api_v1_data_products_material_overview_v2["GET /data-products/material-overview/v2"]
    r__api_v1_data_products_supplier_risk_v1["GET /data-products/supplier-risk/v1"]
    r__api_v1_healthz["GET /healthz"]
    r__api_v1_mappings["POST /mappings"]
    r__api_v1_mappings__mapping_id_["PATCH /mappings/{mapping_id}"]
    r__api_v1_readyz["GET /readyz"]
  end

  subgraph products["Datenprodukte"]
    p_material_overview_1["material-overview<br/>v1 · 1.2 ⚠"]
    p_material_overview_2["material-overview<br/>v2 · 2.0"]
    p_supplier_risk_1["supplier-risk<br/>v1 · 1.0"]
  end

  subgraph repos["Repositories (Ports)"]
    repo_deliveries["deliveries"]
    repo_materials["materials"]
  end

  subgraph sources["Datenquellen (Adapter)"]
    src_neo4j[("neo4j")]
    src_postgres[("postgres")]
  end

  dash --> routes
  r__api_v1_data_products_material_overview_v1 --> p_material_overview_1
  r__api_v1_data_products_material_overview_v2 --> p_material_overview_2
  r__api_v1_data_products_supplier_risk_v1 --> p_supplier_risk_1
  p_material_overview_1 --> repo_materials
  p_material_overview_2 --> repo_materials
  p_supplier_risk_1 --> repo_deliveries
  p_supplier_risk_1 --> repo_materials
  repo_deliveries --> src_postgres
  repo_materials --> src_neo4j

  classDef deprecated stroke-dasharray: 4 3;
  class p_material_overview_1 deprecated;
```

## Versionsstaende

```mermaid
flowchart LR
  subgraph f_material_overview["material-overview"]
    direction LR
    v_material_overview_1["v1 · 1.2<br/>auslaufend<br/>Sunset 2026-12-31"]
    v_material_overview_2["v2 · 2.0<br/>aktiv"]
    v_material_overview_1 -.->|abgeloest durch| v_material_overview_2
  end
  subgraph f_supplier_risk["supplier-risk"]
    direction LR
    v_supplier_risk_1["v1 · 1.0<br/>aktiv"]
  end
```

## Vertraege

Die Felder, auf die sich die Dashboards verlassen.

```mermaid
classDiagram
  class MaterialRowV1 {
    +str material_nr
    +str? bezeichnung
    +str? warengruppe
    +str? werk
    +str? status
    +str? einheit
    +int? bestand
    +str? geaendert
  }
  note for MaterialRowV1 "material-overview v1"
  class MaterialRowV2 {
    +str material_nr
    +str? bezeichnung
    +str? warengruppe
    +str? werk_id
    +str? werk_name
    +str? status
    +int? bestand
    +float? preis
    +float? bestandswert
    +str? geaendert
  }
  note for MaterialRowV2 "material-overview v2"
  class SupplierRiskRow {
    +str lieferant_id
    +str? lieferant_name
    +str? land
    +int anzahl_materialien
    +int lieferungen
    +float liefertreue_pct
    +float mittlerer_verzug_tage
    +float reklamationsquote_pct
    +float risiko_score
    +str risiko_klasse
  }
  note for SupplierRiskRow "supplier-risk v1"
```

## Routeninventar

| Route | Methoden | Produkt | Version | Owner | Cache | Status |
|---|---|---|---|---|---|---|
| `/api/v1/catalog` | GET | – | – | – | – | aktiv |
| `/api/v1/catalog/{name}` | GET | – | – | – | – | aktiv |
| `/api/v1/data-products/material-overview/latest` | GET | material-overview | 2.0 | team-material-management | 60s | Alias |
| `/api/v1/data-products/material-overview/v1` | GET | material-overview | 1.2 | team-material-management | 60s | auslaufend |
| `/api/v1/data-products/material-overview/v2` | GET | material-overview | 2.0 | team-material-management | 60s | aktiv |
| `/api/v1/data-products/supplier-risk/latest` | GET | supplier-risk | 1.0 | team-supply-chain | 300s | Alias |
| `/api/v1/data-products/supplier-risk/v1` | GET | supplier-risk | 1.0 | team-supply-chain | 300s | aktiv |
| `/api/v1/healthz` | GET | – | – | – | – | aktiv |
| `/api/v1/mappings` | POST | – | – | – | – | aktiv |
| `/api/v1/mappings/{mapping_id}` | PATCH | – | – | – | – | aktiv |
| `/api/v1/readyz` | GET | – | – | – | – | aktiv |

## Datenprodukte im Detail

### `material-overview` v1 (1.2)

Materialstammdaten fuer die Uebersichtstabelle

* **Owner:** team-material-management
* **Quelle:** neo4j (ueber materials)
* **Cache:** 60s
* **Filter:** `limit`, `offset`, `status`, `werk`, `warengruppe`, `ohne_klassifizierung`, `suche`
* **Modul:** `data_api/products/catalog/material_overview_v1.py`

### `material-overview` v2 (2.0)

Materialstammdaten inkl. Bestandswert

* **Owner:** team-material-management
* **Quelle:** neo4j (ueber materials)
* **Cache:** 60s
* **Filter:** `limit`, `offset`, `status`, `werk_id`, `warengruppe`, `ohne_klassifizierung`, `suche`, `min_bestandswert`
* **Modul:** `data_api/products/catalog/material_overview_v2.py`

### `supplier-risk` v1 (1.0)

Lieferantenrisiko aus Stammdaten (Neo4j) und Liefertreue (Postgres)

* **Owner:** team-supply-chain
* **Quelle:** neo4j + postgres (ueber deliveries, materials)
* **Cache:** 300s
* **Filter:** `limit`, `offset`, `seit`, `toleranz_tage`, `min_lieferungen`, `risiko_klasse`
* **Modul:** `data_api/products/catalog/supplier_risk_v1.py`

