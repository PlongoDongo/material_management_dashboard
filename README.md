# Data Platform

Monorepo für die Datenplattform: Ingestion in Neo4j/Postgres, ein API-Layer als
Zwischenschicht, und darauf aufbauend mehrere Dashboards für verschiedene Gruppen.

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐     ┌────────────┐
│  Quellen    │ ──► │  ingestion/ │ ──► │   Neo4j /    │ ◄── │    api/    │
│ (SAP, CSV…) │     │             │     │   Postgres   │     │  FastAPI   │
└─────────────┘     └─────────────┘     └──────────────┘     └─────┬──────┘
                                                                   │ HTTP
                                                          ┌────────▼────────┐
                                                          │   frontend/     │
                                                          │  Dash-Apps      │
                                                          └─────────────────┘
```

Die Dashboards sprechen **nicht** direkt mit den Datenbanken. Sie fragen
versionierte *Datenprodukte* über die API ab. Warum das so ist, steht in
[`docs/api_layer_concept.md`](docs/api_layer_concept.md#1-warum-überhaupt-ein-api-layer).

---

## Verzeichnisse

| Pfad | Inhalt | Status |
|---|---|---|
| [`api/`](api/) | FastAPI-Service: Datenprodukte, Cache, Auth. **Code und API-Felder auf Englisch.** | lauffähig, 71 Tests |
| [`frontend/material_management_dashboard/`](frontend/material_management_dashboard/) | Plotly-Dash-Dashboard für Materialstammdaten | lauffähig |
| [`docs/`](docs/) | Konzept, Entwicklerleitfaden, Grundlagen, generierte Diagramme | — |
| `ingestion/` | Befüllung von Neo4j/Postgres aus den Quellsystemen | noch nicht angelegt |

---

## Schnellstart

### API

```bash
cd api
uv venv && uv pip install -e ".[dev]"
cp .env.example .env          # Neo4j-/Postgres-Koordinaten eintragen
.venv/bin/python -m pytest -q # laeuft ohne Datenbank
.venv/bin/uvicorn data_api.main:app --reload --port 8000
```

Interaktive Doku: <http://localhost:8000/docs>

### Dashboard

```bash
cd frontend/material_management_dashboard
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py       # -> http://127.0.0.1:8050
```

### Ohne Datenbank arbeiten

Die **API-Tests** laufen ohne jede Datenbank — sie hängen über
`dependency_overrides` Test-Doubles ein ([`api/tests/fakes.py`](api/tests/fakes.py)).

Der **Server** braucht seine Quellen. Für Quellen, die es noch nicht gibt, legt
ihr Mock-Daten in der Datenbank an statt im Code:

```bash
cd api
python seed/seed_neo4j.py            # jeder Knoten bekommt zusaetzlich das Label :Mock
python seed/seed_neo4j.py --purge    # rueckstandsfrei entfernen
psql "$DSN" -f seed/seed_postgres.sql
```

---

## Dokumentation

| Dokument | Wofür |
|---|---|
| [`docs/api_grundlagen.md`](docs/api_grundlagen.md) | **Für Einsteiger.** Was ist ein Router, eine Session, async? Mit der Historie hinter den heutigen Standards. |
| [`docs/api_development_guide.md`](docs/api_development_guide.md) | **Für Entwickler** (englisch). Rezepte: Datenprodukt hinzufügen, brechende Änderung ausliefern, Datenquelle anbinden. Review-Checkliste. |
| [`docs/api_layer_concept.md`](docs/api_layer_concept.md) | Konzept und Begründung aller Architekturentscheidungen inkl. Alternativen. |
| [`docs/architecture.md`](docs/architecture.md) | Ist-Zustand als Mermaid-Diagramme — **automatisch erzeugt**, nicht von Hand bearbeiten. |
| [`frontend/material_management_dashboard/README.md`](frontend/material_management_dashboard/README.md) | Aufbau des Dashboards, Filter-Mechanik, Layout-Entscheidungen. |

Diagramme neu erzeugen (nach jeder Architekturänderung):

```bash
cd api && .venv/bin/architecture-docs
```

---

## Konventionen

**Zugangsdaten** gehören nie ins Repo. `.env` ist in `.gitignore`; versioniert
wird nur `.env.example`. Die Variablennamen sind in API und Dashboard identisch
(`NEO4J_URI`, `NEO4J_AUTH`, `NEO4J_DB`) — dieselbe `.env` funktioniert für beide.

**Datenprodukte** werden versioniert. Im Pfad steht die Hauptnummer (`/v2`), die
volle Version (`2.1`) in der Antwort. Feld ergänzt → Unternummer, gleiche Route.
Feld entfernt, umbenannt, Typ **oder Bedeutung** geändert → Hauptnummer, neue
Route, alte bleibt bis zum Sunset-Datum. Details im
[Entwicklerleitfaden](docs/api_development_guide.md#7-recipe-release-a-breaking-change).

**Ein neues Dashboard** bekommt einen Ordner unter `frontend/` und spricht
ausschließlich über die API. Als Client-Vorlage dient
[`api/src/data_api/clients/dash_client.py`](api/src/data_api/clients/dash_client.py).

---

## Nächste Schritte

1. Erste echte Quelle anbinden: `NEO4J_URI` setzen, Cypher in
   die `CYPHER`-Konstanten in `api/src/data_api/products/catalog/` an das reale
   Graphmodell anpassen.
   Prüfen lässt sich das an `meta.source` in jeder API-Antwort.
2. Das Material-Management-Dashboard auf den API-Client umstellen — damit entfallen
   dort Cypher und Neo4j-Zugangsdaten.
3. `ingestion/` anlegen, sobald die ersten Quellsysteme liefern.
4. Auth aktivieren, sobald der Identity-Provider feststeht (API-Keys als Zwischenlösung).
