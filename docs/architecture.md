# Architecture (generated)

> This file is generated from the running app by
> `python -m data_api.architecture`. **Do not edit by hand** -- changes are
> lost on the next run. The reasoning behind the design is in
> [`api_layer_concept.md`](api_layer_concept.md).

3 data products · 9 routes

## Data flow

From the route through the data product to the data source.
⚠ marks versions that are being retired.

```mermaid
flowchart LR
  subgraph clients["Konsumenten"]
    dash["Dash-Dashboards"]
  end

  subgraph routes["Routen /api/v1"]
    r__api_v1_catalog["GET /catalog"]
    r__api_v1_catalog__name_["GET /catalog/{name}"]
    r__api_v1_data_products_material_overview_v2["GET /data-products/material-overview/v2 ⚠"]
    r__api_v1_data_products_material_overview_v3["GET /data-products/material-overview/v3"]
    r__api_v1_data_products_supplier_risk_v2["GET /data-products/supplier-risk/v2"]
    r__api_v1_healthz["GET /healthz"]
    r__api_v1_mappings["POST /mappings"]
    r__api_v1_mappings__mapping_id_["PATCH /mappings/{mapping_id}"]
    r__api_v1_readyz["GET /readyz"]
  end

  subgraph products["Data products"]
    p_material_overview_2["material-overview<br/>v2 · 2.1 ⚠"]
    p_material_overview_3["material-overview<br/>v3 · 3.0"]
    p_supplier_risk_2["supplier-risk<br/>v2 · 2.0"]
  end

  subgraph sources["Data sources"]
    src_neo4j[("neo4j")]
    src_postgres[("postgres")]
  end

  dash --> routes
  r__api_v1_data_products_material_overview_v2 --> p_material_overview_2
  r__api_v1_data_products_material_overview_v3 --> p_material_overview_3
  r__api_v1_data_products_supplier_risk_v2 --> p_supplier_risk_2
  p_material_overview_2 --> src_neo4j
  p_material_overview_3 --> src_neo4j
  p_supplier_risk_2 --> src_neo4j
  p_supplier_risk_2 --> src_postgres

  classDef deprecated stroke-dasharray: 4 3;
  class p_material_overview_2 deprecated;
```

## Version states

```mermaid
flowchart LR
  subgraph f_material_overview["material-overview"]
    direction LR
    v_material_overview_2["v2 · 2.1<br/>retiring<br/>Sunset 2026-12-31"]
    v_material_overview_3["v3 · 3.0<br/>active"]
    v_material_overview_2 -.->|superseded by| v_material_overview_3
  end
  subgraph f_supplier_risk["supplier-risk"]
    direction LR
    v_supplier_risk_2["v2 · 2.0<br/>active"]
  end
```

## Contracts

The fields the dashboards rely on.

```mermaid
classDiagram
  class MaterialRowV2 {
    +str material_number
    +str? description
    +str? material_group
    +str? plant
    +str? status
    +str? unit
    +int? stock
    +str? changed_on
  }
  note for MaterialRowV2 "material-overview v2"
  class MaterialRowV3 {
    +str material_number
    +str? description
    +str? material_group
    +str? plant_id
    +str? plant_name
    +str? status
    +int? stock
    +float? price
    +float? stock_value
    +str? changed_on
  }
  note for MaterialRowV3 "material-overview v3"
  class SupplierRiskRow {
    +str supplier_id
    +str? supplier_name
    +str? country
    +int material_count
    +int deliveries
    +float? on_time_rate_pct
    +float? avg_delay_days
    +float? complaint_rate_pct
    +float? risk_score
    +str risk_class
  }
  note for SupplierRiskRow "supplier-risk v2"
```

## Route inventory

| Route | Methods | Product | Version | Owner | Cache | Status |
|---|---|---|---|---|---|---|
| `/api/v1/catalog` | GET | – | – | – | – | active |
| `/api/v1/catalog/{name}` | GET | – | – | – | – | active |
| `/api/v1/data-products/material-overview/latest` | GET | material-overview | 3.0 | team-material-management | 60s | alias |
| `/api/v1/data-products/material-overview/v2` | GET | material-overview | 2.1 | team-material-management | 60s | retiring |
| `/api/v1/data-products/material-overview/v3` | GET | material-overview | 3.0 | team-material-management | 60s | active |
| `/api/v1/data-products/supplier-risk/latest` | GET | supplier-risk | 2.0 | team-supply-chain | 300s | alias |
| `/api/v1/data-products/supplier-risk/v2` | GET | supplier-risk | 2.0 | team-supply-chain | 300s | active |
| `/api/v1/healthz` | GET | – | – | – | – | active |
| `/api/v1/mappings` | POST | – | – | – | – | active |
| `/api/v1/mappings/{mapping_id}` | PATCH | – | – | – | – | active |
| `/api/v1/readyz` | GET | – | – | – | – | active |

## Data products in detail

### `material-overview` v2 (2.1)

Material master data for the overview table

* **Owner:** team-material-management
* **Sources:** neo4j
* **Cache:** 60s
* **Filters:** `limit`, `offset`, `status`, `plant`, `material_group`, `unclassified_only`, `search`
* **Module:** `data_api/products/catalog/material_overview_v2.py`

### `material-overview` v3 (3.0)

Material master data including stock value

* **Owner:** team-material-management
* **Sources:** neo4j
* **Cache:** 60s
* **Filters:** `limit`, `offset`, `status`, `plant_id`, `material_group`, `unclassified_only`, `search`, `min_stock_value`
* **Module:** `data_api/products/catalog/material_overview_v3.py`

### `supplier-risk` v2 (2.0)

Supplier risk from master data (Neo4j) and delivery reliability (Postgres)

* **Owner:** team-supply-chain
* **Sources:** neo4j + postgres
* **Cache:** 300s
* **Filters:** `limit`, `offset`, `since`, `tolerance_days`, `min_deliveries`, `risk_class`, `country`
* **Module:** `data_api/products/catalog/supplier_risk_v2.py`

