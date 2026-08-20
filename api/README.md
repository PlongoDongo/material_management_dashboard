# API-Layer (FastAPI)

Zwischenschicht zwischen den Dash-Dashboards und den Datenquellen (Neo4j,
Postgres, später weitere Services).

Dieses README ist die Kurzfassung zum Loslegen. Ausführlicher:

| Dokument | Wofür |
|---|---|
| [`docs/api_development_guide.md`](../docs/api_development_guide.md) | **Entwicklerleitfaden** (englisch) — Rezepte zum Erweitern, Konventionen, Review-Checkliste |
| [`docs/api_layer_concept.md`](../docs/api_layer_concept.md) | Konzept und Begründungen der Architekturentscheidungen |
| [`docs/api_grundlagen.md`](../docs/api_grundlagen.md) | Grundlagen für Einsteiger — was ist ein Router, eine Session, async? Inklusive Historie |
| [`docs/architecture.md`](../docs/architecture.md) | Automatisch erzeugte Diagramme des Ist-Zustands |

---

## Schnellstart

Die **Tests** laufen ohne Datenbank (Test-Doubles via `dependency_overrides`).
Der **Server** braucht seine Quellen: fehlt `NEO4J_URI` bzw. `POSTGRES_DSN`,
melden die betroffenen Datenprodukte einen Konfigurationsfehler und `/readyz`
meldet 503.

Für Quellen, die es noch nicht gibt, legt ihr Mock-Daten **in der Datenbank** an
statt im Code — siehe [`seed/`](seed/).

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/uvicorn data_api.main:app --reload --port 8000
```

```bash
.venv/bin/python -m pytest -q
```

Dann im Browser: <http://localhost:8000/docs>

```bash
curl -s localhost:8000/api/v1/catalog | python -m json.tool
```

---

## Die zentralen Begriffe in 60 Sekunden

**Datenprodukt** – ein benannter, versionierter Vertrag über einen Datensatz.
Nicht „eine Route, die zufällig die DB abfragt". Hat ein Schema, einen Owner,
eine Cache-Dauer und einen Lebenszyklus.

**Zwei Versionsachsen** – die verwechselt man leicht:

```
/api/v1/data-products/material-overview/v2
 ^^^^^^                                 ^^
 API-Version (Transport:                Datenprodukt-Version
 Fehlerformat, Auth, Umschlag)          (Felder dieser Tabelle)
```

Im Pfad steht nur das **MAJOR** (`v2`), die volle Version (`2.1`) steht in
`meta.version` der Antwort. Feld ergänzt → MINOR, gleiche Route. Feld entfernt,
umbenannt, Typ oder *Bedeutung* geändert → MAJOR, neue Route, alte bleibt bis
zum Sunset-Datum.

**Treiber vs. Session** – Treiber/Engine leben den ganzen Prozess (Lifespan),
Sessions genau einen Request (Depends). Nie umgekehrt.

---

## Verzeichnisse

| Pfad | Inhalt |
|---|---|
| `src/data_api/products/catalog/` | **Hier kommen neue Datenprodukte rein** — eine Datei pro Produkt und Major-Version |
| `src/data_api/products/` | Das Framework: Registry, Routen-Generator, Cache, Basismodelle |
| `src/data_api/repositories/` | Datenzugriff je Fachlichkeit: Port (Protocol) + Neo4j-/SQL-Adapter |
| `src/data_api/db/` | Treiber-/Engine-Lebenszyklus, `Repositories`-Container pro Request |
| `src/data_api/api/v1/` | Handgeschriebene Router (Health, Katalog, Schreibseite) |
| `src/data_api/core/` | Settings, Logging, Fehlerformat, Auth |
| `src/data_api/clients/` | Client-Vorlage für die Dash-Apps |
| `seed/` | Mock-Daten für Neo4j/Postgres, solange Quellen fehlen |
| `tests/fakes.py` | Test-Doubles — die einzigen Beispieldaten im Repo |

---

## Endpunkte

```
GET   /api/v1/healthz                                  Liveness (prüft nur den Prozess)
GET   /api/v1/readyz                                   Readiness (prüft die Datenquellen)
GET   /api/v1/catalog                                  alle Datenprodukte + Versionen
GET   /api/v1/catalog/{name}                           ein Produkt im Detail
GET   /api/v1/data-products/{name}/v{major}            die Daten
GET   /api/v1/data-products/{name}/latest              Alias (nicht für Dashboards!)
POST  /api/v1/mappings                                 Beispiel Schreibseite
PATCH /api/v1/mappings/{id}
```

Antwortformat aller Datenprodukte:

```json
{
  "meta": {"product": "...", "version": "2.0", "generated_at": "...",
           "row_count": 64, "total_count": 64, "source": "neo4j",
           "cache": "hit", "deprecated": false, "sunset": null},
  "data": [ ... ]
}
```

`meta.source` zeigt, aus welchen Quellen die Antwort zusammengesetzt wurde.

---

## Neues Datenprodukt anlegen

Eine Datei in `src/data_api/products/catalog/`, sonst nichts:

```python
@data_product(
    name="werk-auslastung", version="1.0",
    summary="Materialien und Bestand je Werk",
    item_model=WerkRow, params_model=WerkParams,
    owner="team-material-management", cache_ttl=120,
)
async def load(repos: Repositories, params: WerkParams):
    repo = await repos.materials()
    return transform(await repo.fetch_materials(), params)
```

Nach dem Neustart existieren automatisch: Route, `/latest`-Alias,
OpenAPI-Eintrag mit Schema, Katalogeintrag, Caching, ETag, Paginierung,
Fehlerformat und Auth-Prüfung. Kein Router wird angefasst.

Konvention pro Datei — immer in dieser Reihenfolge:

1. **Row-Modell** → der Vertrag
2. **Params-Modell** → die erlaubten Filter
3. **`transform()`** → reine Funktion, ohne I/O — hier liegt die Fachlichkeit
4. **`load()`** → holt Rohdaten aus den Repositories, ruft `transform()`

Die Trennung von 3 und 4 ist der Grund, warum die Fachlogik ohne Datenbank
testbar ist (`tests/test_transformations.py`).

---

## Architekturdiagramm erzeugen

Die visuelle Dokumentation wird aus der **laufenden App** erzeugt, nicht von Hand
gepflegt:

```bash
.venv/bin/architecture-docs            # schreibt ../docs/architecture.md
.venv/bin/architecture-docs --check    # CI: schlägt fehl, wenn veraltet
```

Ergebnis: [`../docs/architecture.md`](../docs/architecture.md) mit drei
Mermaid-Diagrammen (Datenfluss Route → Produkt → Repository → Quelle,
Versionsstände, Vertragsschemata), Routeninventar und Steckbrief je Produkt.

Die Verbindungen werden abgeleitet, nicht gepflegt:

| Information | Quelle |
|---|---|
| Routen, Methoden, Deprecation | `app.openapi()` |
| Version, Owner, Cache, Vertragsfelder | die Registry |
| Produkt → Repository | AST des Loaders (`repos.X()`-Aufrufe) |
| Repository → Datenquelle | AST des `Repositories`-Containers + `source`-Attribut der Adapter |

`tests/test_architecture.py::test_dokumentation_ist_aktuell` sorgt dafür, dass
niemand ein Produkt anlegt und das Diagramm veralten lässt.

Optional, prüft die Mermaid-Syntax mit dem echten Parser:

```bash
npm install mermaid jsdom && node tools/validate_mermaid.mjs ../docs/architecture.md
```

---

## Konfiguration

`cp .env.example .env` und anpassen. Die Neo4j-Variablen sind absichtlich
identisch zu denen des Dashboards — dieselbe `.env` funktioniert für beide.

| Variable | Bedeutung |
|---|---|
| `NEO4J_URI`, `NEO4J_AUTH`, `NEO4J_DB` | Pflicht — leer → 503 bei `/readyz` |
| `POSTGRES_DSN` | muss `postgresql+asyncpg://` sein (async!) |
| `API_CORS_ORIGINS` | Ports der Dash-Apps, kommagetrennt |
| `API_KEYS` | leer = Auth aus (nur dev) |

---

## Dashboard anbinden

`src/data_api/clients/dash_client.py` ist die Vorlage. Im Dashboard ändert sich
nur `data/repository.py`:

```python
from data_api.clients.dash_client import DataProductClient
_client = DataProductClient()          # einmal pro Prozess

def load_materials() -> pl.DataFrame:
    rows, meta = _client.fetch("material-overview", "v2", limit=50_000)
    return pl.DataFrame(rows)
```

`data/neo4j.py` und der Cypher im Dashboard entfallen — inklusive der
Neo4j-Zugangsdaten in der Dashboard-Umgebung.

Die Version wird **fest** angegeben, nicht `latest`: Ein Versionswechsel soll im
Git-Diff auftauchen, nicht still passieren.

---

## Tests

```
tests/test_transformations.py   Fachlogik pur, ohne DB und HTTP   ← die meisten Tests
tests/test_registry.py          Registry-Regeln (Versionskollisionen etc.)
tests/test_data_products.py     Ende-zu-Ende über HTTP
tests/test_health.py            Betriebsendpunkte
tests/test_architecture.py      Diagramm-Generator + Veraltungs-Check
tests/fakes.py                  Test-Doubles (kein Test, sondern Werkzeug)
```

Alle laufen ohne Datenbank und ohne Docker: `conftest.py` hängt über
`app.dependency_overrides[get_repositories]` einen Fake ein. Ersetzt wird nur
die unterste Schicht — alles darüber läuft unverändert.

## Mock-Daten für fehlende Quellen

```bash
python seed/seed_neo4j.py            # anlegen (jeder Knoten trägt Label :Mock)
python seed/seed_neo4j.py --purge    # wieder entfernen
psql "$DSN" -f seed/seed_postgres.sql
```

Details in [`seed/README.md`](seed/README.md).
