# Konzept: API-Layer zwischen Dashboards und Datenquellen

Status: Entwurf v1 · Referenzimplementierung unter [`api/`](../api/) · 47 Tests grün

**Dieses Dokument begründet die Entwürfe.** Je nach Frage passt eines der anderen besser:

| Frage | Dokument |
|---|---|
| „Wie füge ich ein Datenprodukt hinzu?" | [api_development_guide.md](api_development_guide.md) (englisch) |
| „Warum macht man das überhaupt so?" | [api_grundlagen.md](api_grundlagen.md) — Grundlagen und Historie |
| „Wie sieht der Ist-Zustand aus?" | [architecture.md](architecture.md) — automatisch erzeugt |

---

## Inhalt

1. [Warum überhaupt ein API-Layer](#1-warum-überhaupt-ein-api-layer)
2. [Das Leitbild: Datenprodukt statt Endpunkt](#2-das-leitbild-datenprodukt-statt-endpunkt)
3. [Schichtenmodell und Verzeichnisstruktur](#3-schichtenmodell-und-verzeichnisstruktur)
4. [Route-Management](#4-route-management)
5. [Versionierung](#5-versionierung)
6. [Datenbankverbindung und Session-Management](#6-datenbankverbindung-und-session-management)
7. [Transformationen](#7-transformationen)
8. [Antwortformat](#8-antwortformat)
9. [Schreibende Endpunkte](#9-schreibende-endpunkte)
10. [Caching und Performance](#10-caching-und-performance)
11. [Fehlerbehandlung](#11-fehlerbehandlung)
12. [Konfiguration und Secrets](#12-konfiguration-und-secrets)
13. [Authentifizierung und RBAC](#13-authentifizierung-und-rbac)
14. [Testing](#14-testing)
15. [Betrieb und Deployment](#15-betrieb-und-deployment)
16. [Anbindung der Dashboards](#16-anbindung-der-dashboards)
17. [Ein neues Datenprodukt anlegen](#17-ein-neues-datenprodukt-anlegen)
18. [Automatische Architekturdokumentation](#18-automatische-architekturdokumentation)
19. [Roadmap und offene Entscheidungen](#19-roadmap-und-offene-entscheidungen)

---

## 1. Warum überhaupt ein API-Layer

Heute greift das Material-Management-Dashboard direkt auf Neo4j zu
(`material_management_dashboard/data/repository.py`). Für ein Dashboard geht das
gut. Für fünf Dashboards und mehrere Teams entstehen vier Probleme:

| Problem ohne API-Layer | Konsequenz |
|---|---|
| Jedes Dashboard kennt Cypher und die Zugangsdaten | Graphmodell-Änderung = alle Dashboards anfassen; Credentials liegen fünffach herum |
| Berechnungslogik wird kopiert | Zwei Dashboards zeigen zwei verschiedene Zahlen für dieselbe Kennzahl |
| Jedes Dashboard hält eigene DB-Verbindungen | Der Connection-Pool von Neo4j wird zum Engpass, niemand weiß, wer ihn belastet |
| Keine gemeinsame Sicht darauf, was es gibt | „Hat das schon jemand gebaut?" ist nur mündlich beantwortbar |

Der API-Layer löst das, indem er **eine** Stelle wird, an der Datenzugriff,
Berechnung und Berechtigung stattfinden. Die Dashboards werden zu reinen
Darstellungsschichten, die HTTP sprechen — und damit auch für andere Konsumenten
nutzbar (Notebooks, Excel-Power-Query, ein späterer Export-Service).

**Was der Layer ausdrücklich *nicht* ist:** keine generische
„Datenbank-über-HTTP"-Schicht. Ein Endpunkt, der beliebiges Cypher entgegennimmt,
verlagert das Problem nur und macht Caching, Berechtigungen und Versionierung
unmöglich.

---

## 2. Das Leitbild: Datenprodukt statt Endpunkt

Der zentrale Begriff des Konzepts. Ein **Datenprodukt** ist nicht „eine Route,
die zufällig die Datenbank abfragt", sondern ein benannter, versionierter
Vertrag mit einem Besitzer:

```
name         material-overview          stabiler fachlicher Name
version      2.0                        MAJOR.MINOR
item_model   MaterialRowV2              DER Vertrag: Felder, Typen, Pflicht/Optional
params_model MaterialParamsV2           erlaubte Filter, typisiert
loader       async (repos, params)      Query + Transformation
owner        team-material-management   wen fragt man
cache_ttl    60                         wie frisch muss es sein
```

Konsequenzen dieser Definition:

* **Das Schema ist der Vertrag, nicht die Query.** Wenn sich das Graphmodell
  ändert, aber die gelieferten Felder gleich bleiben, merkt kein Dashboard etwas.
* **Jedes Produkt hat einen Owner.** Bei „warum ist die Zahl so?" gibt es eine
  zuständige Person, nicht eine Suche durch fünf Repositories.
* **Ein Produkt ist auffindbar.** Der Katalog (`/api/v1/catalog`) wird aus
  derselben Registry erzeugt wie die Routen und kann deshalb nicht veralten.

---

## 3. Schichtenmodell und Verzeichnisstruktur

### Die Schichten

```
     HTTP-Request
          │
    ┌─────▼──────────────────────────────────────────────┐
    │ Router-Schicht      api/v1/*, products/router.py    │  Transport:
    │                                                     │  Pfade, Statuscodes,
    │                                                     │  Header, OpenAPI
    └─────┬──────────────────────────────────────────────┘
          │  geparste, validierte Parameter
    ┌─────▼──────────────────────────────────────────────┐
    │ Produkt-Schicht     products/catalog/*.py           │  Fachlichkeit:
    │   loader()   ── orchestriert                        │  welche Quellen,
    │   transform() ── REIN, ohne I/O  ← hier liegt der   │  welche Formel
    │                                    Wert und die     │
    │                                    Testabdeckung    │
    └─────┬──────────────────────────────────────────────┘
          │  „gib mir die Rohzeilen"
    ┌─────▼──────────────────────────────────────────────┐
    │ Repository-Schicht  repositories/*.py               │  Datenzugriff:
    │   Port (Protocol) + Adapter (Neo4j / SQL)            │  Cypher, SQL
    └─────┬──────────────────────────────────────────────┘
          │  Session
    ┌─────▼──────────────────────────────────────────────┐
    │ Infrastruktur       db/neo4j.py, db/sql.py          │  Treiber, Pools,
    │                                                     │  Lebenszyklus
    └────────────────────────────────────────────────────┘
```

**Die eine Regel, die alles zusammenhält:** Abhängigkeiten zeigen nur nach unten.
Ein Repository weiß nichts von FastAPI. Eine `transform()`-Funktion weiß nichts
von Datenbanken. Deshalb ist die Fachlichkeit in Millisekunden testbar — ohne
Server, ohne Datenbank, ohne Docker.

### Verzeichnisstruktur

```
api/
├── pyproject.toml
├── .env.example
├── README.md
├── migrations/                     # Alembic (nur für die Postgres-Schreibseite)
├── src/
│   └── data_api/                   # ← echtes, installierbares Paket
│       ├── main.py                 # uvicorn data_api.main:app
│       ├── application.py          # create_app() + Lifespan
│       │
│       ├── core/                   # querschnittlich, kennt keine Fachlichkeit
│       │   ├── config.py           #   Settings (pydantic-settings)
│       │   ├── logging.py          #   Logging + Request-ID-Kontext
│       │   ├── middleware.py       #   Request-ID, Laufzeitmessung
│       │   ├── errors.py           #   AppError-Hierarchie + Problem Details
│       │   └── security.py         #   Principal, Auth-Dependency
│       │
│       ├── db/                     # Infrastruktur
│       │   ├── neo4j.py            #   Treiber erzeugen/schließen
│       │   ├── sql.py              #   Engine + Sessionmaker
│       │   └── repositories.py     #   Repositories-Container (Request-Scope)
│       │
│       ├── repositories/           # Port + Adapter je Datenquelle
│       │   ├── materials.py        #   Protocol + Neo4j-Adapter
│       │   └── deliveries.py       #   Protocol + SQL-Adapter
│       │
│       ├── products/               # das Datenprodukt-Framework
│       │   ├── base.py             #   DataProduct, Envelope, Meta, Params
│       │   ├── registry.py         #   Registry + @data_product + Auto-Discovery
│       │   ├── router.py           #   erzeugt typisierte Routen aus der Registry
│       │   ├── cache.py            #   TTL-Cache + ETag
│       │   └── catalog/            #   ← HIER kommen neue Produkte rein
│       │       ├── material_overview_v1.py
│       │       ├── material_overview_v2.py
│       │       └── supplier_risk_v1.py
│       │
│       ├── api/                    # handgeschriebene Router
│       │   ├── deps.py             #   gemeinsame Dependencies
│       │   └── v1/
│       │       ├── __init__.py     #   baut die API-Version v1 zusammen
│       │       ├── health.py       #   /healthz, /readyz
│       │       ├── catalog.py      #   /catalog
│       │       └── mappings.py     #   Schreibseite (POST/PATCH)
│       │
│       └── clients/
│           └── dash_client.py      # Vorlage für die Dash-Apps
└── tests/
```

### Anmerkungen zur bereits begonnenen Struktur der Kollegen

Die vorhandene Struktur (`src/api/routes.py`, `src/db/`, `src/models/`,
`src/services/`) ist ein solider Start und in vielen FastAPI-Tutorials genau so
zu finden. Vier Punkte würde ich ändern, bevor viel Code entsteht:

1. **`src/` braucht einen Paketnamen.** Ohne `src/data_api/` gibt es keinen
   eindeutigen Importpfad; Imports funktionieren dann je nach Arbeitsverzeichnis
   mal so, mal so. Ein installierbares Paket (`pip install -e .`) beendet diese
   Klasse von Fehlern dauerhaft.

2. **Eine einzelne `routes.py` skaliert nicht.** Nach dem fünften Dashboard ist
   das eine Datei mit 800 Zeilen und permanenten Merge-Konflikten, weil alle
   Teams daran arbeiten. Besser: ein Router pro Thema, plus generierte Routen für
   die Datenprodukte.

3. **`models/db_models.py` vermischt zwei Dinge.** ORM-Modelle (wie liegt es in
   der Datenbank) und API-Schemata (was schicken wir raus) sollten getrennt sein.
   Sonst ist jede Datenbankspalten-Umbenennung automatisch eine brechende
   API-Änderung. Hier: ORM-Modelle nur für die Schreibseite, API-Schemata liegen
   beim jeweiligen Datenprodukt.

4. **`services/neo4j_services.py` schneidet nach Technologie, nicht nach
   Fachlichkeit.** Eine Datei, in der jeder Neo4j-Code landet, wird zum zweiten
   Sammelbecken neben `routes.py`. Besser: Schnitt nach Fachlichkeit
   (`materials`, `deliveries`), und *innerhalb* davon nach Technologie.

Der Rest — `db/`, `migrations/`, `tests/`, getrennte `app.py`/`main.py` — bleibt
im Konzept erhalten.

---

## 4. Route-Management

### Wie es in FastAPI normalerweise gemacht wird

`APIRouter` ist das Werkzeug. Ein Router bündelt zusammengehörige Endpunkte mit
gemeinsamem Prefix und gemeinsamen Tags; eine zentrale Stelle steckt sie zusammen:

```python
# api/v1/health.py
router = APIRouter(tags=["Betrieb"])

@router.get("/healthz")
async def healthz(): ...

# api/v1/__init__.py  — die EINE Stelle, die alles zusammensteckt
def build_v1_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    router.include_router(health.router)
    router.include_router(catalog.router)
    router.include_router(build_products_router())
    router.include_router(mappings.router)
    return router
```

Wichtig ist die Richtung: **kein Router importiert einen anderen, keiner kennt
die App.** Deshalb gibt es hier nie Zirkelimporte, egal wie viele Router
dazukommen. Das ist derselbe Gedanke wie `register_*_callbacks(app)` im
bestehenden Dashboard.

### Die Besonderheit hier: generierte Routen

Für Datenprodukte schreiben wir die Routen nicht von Hand, sondern erzeugen sie
beim Start aus der Registry. Die naheliegende Alternative wäre eine einzige
generische Route:

```python
@router.get("/data-products/{name}/{version}")   # ← so NICHT
async def get_product(name: str, version: str): ...
```

Die funktioniert, kostet aber genau das, wofür man FastAPI nimmt: In `/docs`
stünde dann nur „gibt irgendein JSON zurück". Niemand könnte nachschlagen, welche
Felder ein Produkt liefert, und es ließen sich keine Clients generieren.

Stattdessen erzeugt [`products/router.py`](../api/src/data_api/products/router.py)
pro (Produkt, Major-Version) eine echte Route mit eigenem `response_model`.
Ergebnis: vollständige OpenAPI-Dokumentation **und** „neues Produkt = neue Datei".

> **Fallstrick, der uns fast getroffen hätte:** In dem Modul, das Endpunkte
> dynamisch erzeugt, darf kein `from __future__ import annotations` stehen. Die
> Typannotationen der erzeugten Funktionen sind Laufzeitobjekte aus der Closure;
> mit der Future-Zeile werden sie zu Strings und FastAPI kann sie nicht mehr
> auflösen → `TypeError` beim Start.

### Resultierende Routen

```
GET   /api/v1/healthz                                       Liveness
GET   /api/v1/readyz                                        Readiness (prüft DBs)
GET   /api/v1/catalog                                       alle Datenprodukte
GET   /api/v1/catalog/{name}                                ein Produkt, alle Versionen
GET   /api/v1/data-products/material-overview/v1            [deprecated]
GET   /api/v1/data-products/material-overview/v2
GET   /api/v1/data-products/material-overview/latest        Alias
GET   /api/v1/data-products/supplier-risk/v1
POST  /api/v1/mappings                                      Schreibseite
PATCH /api/v1/mappings/{mapping_id}
```

---

## 5. Versionierung

Das ist der Teil mit dem größten langfristigen Effekt — und der, bei dem die
meisten Projekte durcheinanderkommen. Der Schlüssel: **es gibt zwei unabhängige
Versionsachsen.**

```
/api/v1/data-products/material-overview/v2
 ^^^^^^                                 ^^
 API-Version                            Datenprodukt-Version
 Transportvertrag:                      Datenvertrag:
 Fehlerformat, Auth, Umschlag,          welche Felder hat DIESE eine
 Pfadaufbau                             Tabelle
```

Sie ändern sich unabhängig voneinander. Ein neues Feld in einem Datenprodukt darf
nicht die ganze API auf v2 heben, und ein geänderter Auth-Mechanismus darf nicht
jedes Datenprodukt neu versionieren. Wer beide in eine Nummer presst, versioniert
am Ende alles gleichzeitig oder gar nicht.

### Die Regel für Datenprodukte

| Änderung | Version | Route | Für das Dashboard |
|---|---|---|---|
| Feld **hinzugefügt** | MINOR (`2.0` → `2.1`) | bleibt `/v2` | unkritisch — unbekannte Felder werden ignoriert |
| Doku, Performance, Bugfix ohne Formeländerung | MINOR | bleibt `/v2` | unkritisch |
| Feld **entfernt** oder **umbenannt** | MAJOR (`2.x` → `3.0`) | neu: `/v3` | Migration nötig |
| Feld**typ** geändert (`str` → `int`) | MAJOR | neu: `/v3` | Migration nötig |
| Bedeutung eines Feldes geändert (Formel!) | MAJOR | neu: `/v3` | Migration nötig — sonst zeigt das Dashboard stillschweigend etwas anderes an |

Deshalb steht **im Pfad nur das MAJOR** (`/v2`) und **in der Antwort die volle
Version** (`meta.version = "2.1"`). Ein Dashboard, das gegen `/v2` gebaut ist,
kann durch einen Minor-Release nicht brechen — und trotzdem ist in jeder Antwort
und in jedem Log nachvollziehbar, welcher exakte Stand geliefert wurde.

Die letzte Zeile der Tabelle ist die gefährlichste: Wenn sich die *Berechnung*
hinter `risiko_score` ändert, ist das Schema identisch, aber die Zahlen bedeuten
etwas anderes. Das ist eine brechende Änderung, auch wenn kein Typ sich rührt.

### Warum Pfad-Versionierung und nicht Header

| Ansatz | Beispiel | Bewertung |
|---|---|---|
| **Pfad** | `/data-products/x/v2` | ✅ **Empfehlung.** Sichtbar in Logs, Browser, curl, Netzwerk-Tab. Trivial zu cachen und im Reverse Proxy zu routen. Leicht zu erklären. |
| Header | `Accept: application/vnd.airbus.v2+json` | Formal eleganter (eine Ressource, mehrere Repräsentationen). In der Praxis: nicht im Browser testbar, Caches brauchen `Vary`, Debugging ist mühsam. |
| Query-Parameter | `?version=2` | Vermischt Versionierung mit Filterung; leicht zu vergessen → welche Version ist dann Default? |

Für ein internes Analytics-Backend mit Dash-Clients ist der Pfad die richtige Wahl.

### Lebenszyklus einer Version

```
        v1 live                     v1 deprecated                v1 entfernt
   ────────────────►  v2 live  ───────────────────►  Sunset  ──────────────►
                      beide       Deprecation- +     Datum      Datei
                      parallel    Sunset-Header      erreicht   gelöscht
```

Die Referenzimplementierung setzt das um:

```python
@data_product(
    name="material-overview", version="1.2", ...,
    deprecated=True,
    sunset=date(2026, 12, 31),
)
```

Das erzeugt automatisch die Antwortheader nach RFC 8594:

```
Deprecation: true
Sunset: Thu, 31 Dec 2026 00:00:00 GMT
X-Data-Product-Version: 1.2
```

Der mitgelieferte Dash-Client loggt eine Warnung, sobald er ein deprecated
Produkt bezieht. Vor dem Löschen: in den Zugriffslogs prüfen, ob `/v1` wirklich
niemand mehr abfragt. Danach wird die Datei `material_overview_v1.py` gelöscht —
Route, Doku und Katalogeintrag verschwinden automatisch mit.

**Wichtig:** Solange `v1` existiert, wird `v1` **nicht** verändert. Der häufigste
Weg, auf dem Versionierung scheitert, ist „wir patchen v1 noch schnell, es nutzt
ja nur noch ein Dashboard".

### `latest`

Existiert als Alias, ist aber **nicht für Dashboards gedacht** — sonst wandert
ein brechendes v2 unangekündigt in die Produktion. Dashboards pinnen eine feste
Version; die soll bei einem Update im Git-Diff auftauchen. `latest` ist für
Exploration, Notebooks und `/docs`.

---

## 6. Datenbankverbindung und Session-Management

Das Thema mit den meisten Missverständnissen. Die Grundregel ist bei Neo4j und
SQLAlchemy identisch, nur die Begriffe unterscheiden sich:

| | Neo4j | SQLAlchemy | Lebensdauer | Anzahl |
|---|---|---|---|---|
| Verbindungspool-Halter | `Driver` | `Engine` | **ganzer Prozess** | genau 1 |
| Arbeitseinheit | `Session` | `Session` | **ein Request** | 1 pro Request |

**Zwei klassische Fehler:**

1. *Treiber pro Request bauen.* Wirft den Connection-Pool weg — jeder Request
   zahlt TCP- plus TLS-Handshake. Sieht in der Entwicklung nicht auf, killt aber
   unter Last die Latenz.
2. *Eine prozessweite Session.* Sessions sind nicht thread-safe. Das produziert
   sporadische, nicht reproduzierbare Fehler unter Parallellast — die
   unangenehmste Fehlerklasse überhaupt.

### Wo was lebt

```python
# application.py — Lifespan: läuft einmal beim Start und einmal beim Stoppen
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.neo4j_driver = await create_driver(...)      # ← prozessweit
    app.state.sql_engine = create_engine(...)
    app.state.sql_sessionmaker = create_sessionmaker(...)
    yield
    await close_driver(app.state.neo4j_driver)             # ← sauberes Herunterfahren
    await dispose_engine(app.state.sql_engine)
```

```python
# api/deps.py — Dependency: läuft einmal pro Request
async def get_repositories(request, settings) -> AsyncIterator[Repositories]:
    async with AsyncExitStack() as stack:
        yield Repositories(stack=stack, ...)               # ← alles hierin wird
                                                           #   automatisch geschlossen
```

Merksatz:

> **Lifespan** → alles, was den ganzen Prozess lang lebt (Treiber, Engine, Pools).
> **Depends** → alles, was einen Request lang lebt (Sessions, Aufrufer).

Warum `lifespan` und nicht `@app.on_event("startup")`: Letzteres ist in FastAPI
deprecated und kennt keinen gemeinsamen Zustand zwischen Hoch- und Abbauteil.

Warum `app.state` und nicht Modul-Globals: Damit zwei Apps (etwa in Tests) nicht
denselben Treiber teilen.

### Der `Repositories`-Container

Ein Datenprodukt sieht nicht die Sessions, sondern einen Container
([`db/repositories.py`](../api/src/data_api/db/repositories.py)):

```python
async def load(repos: Repositories, params):
    materials = await repos.materials()      # Neo4j-Session öffnet sich hier, lazy
    deliveries = await repos.deliveries()    # SQL-Session öffnet sich hier, lazy
    ...
```

Er leistet vier Dinge:

* **Lazy**: Ein Produkt, das nur Neo4j braucht, öffnet keine Postgres-Verbindung.
* **Pro Request gecacht**: Zwei Repositories teilen sich eine Session.
* **Automatisch geschlossen**: Der `AsyncExitStack` räumt auf — auch wenn der
  Endpunkt eine Exception wirft. Niemand schreibt jemals `session.close()`.

### async oder sync?

Wir nutzen die **asynchronen** Treiber (`AsyncGraphDatabase`,
`create_async_engine` mit `postgresql+asyncpg://`), weil ein API-Gateway fast nur
auf I/O wartet.

Der Fallstrick dabei: **Mischen ist gefährlich.** Ein blockierender Aufruf (der
synchrone Neo4j-Treiber, `requests`, `time.sleep`) in einer `async def`-Funktion
blockiert den kompletten Event-Loop — also *alle* parallelen Requests, nicht nur
den eigenen.

Wer beim synchronen Treiber bleiben will, schreibt die Endpunkte als `def` statt
`async def`; FastAPI schiebt sie dann automatisch in einen Threadpool. Das ist
eine legitime Wahl (weniger Fallstricke), skaliert aber schlechter.

### Wenn eine Datenquelle fehlt

Es gibt **keinen** Ersatzdatensatz im Produktionspfad. Fehlt `NEO4J_URI`, melden
alle Datenprodukte, die den Graphen brauchen, einen `ConfigurationError` (HTTP
500), und `/readyz` meldet 503, sodass der Loadbalancer den Pod aus dem Verkehr
nimmt.

Das ist eine bewusste Entscheidung. Eine API, die stillschweigend erfundene
Zahlen liefert, ist gefährlicher als eine, die ehrlich einen Fehler meldet —
und ein Ersatzpfad, der nie in Produktion laufen soll, ist Code, den man später
wieder ausbauen muss.

Mock-Daten für die noch fehlenden Quellen gehören stattdessen **in die
Datenbank** (siehe [`api/seed/`](../api/seed/)):

```bash
python seed/seed_neo4j.py            # 64 Materialien, 4 Lieferanten, Label :Mock
psql "$DSN" -f seed/seed_postgres.sql
```

Drei Vorteile gegenüber Beispieldaten im Anwendungscode:

* Die API bleibt schlank — ein Codepfad, kein Schalter, kein Sicherheitsnetz.
* Der **echte** Weg wird geübt: Cypher, Treiber, Session, Typkonvertierung.
  Beispieldaten im Code überspringen genau die Stellen, an denen es später knallt.
* Zum Ausbauen löscht man Knoten statt Code. Jeder Seed-Knoten trägt dafür das
  Label `:Mock`:

  ```cypher
  MATCH (n:Mock) DETACH DELETE n
  ```

Für **Tests** gilt das Gegenteil: sie dürfen keine laufende Datenbank brauchen.
Dafür gibt es Test-Doubles in [`api/tests/fakes.py`](../api/tests/fakes.py), die
über `dependency_overrides` eingehängt werden — siehe Abschnitt 14.

## 7. Transformationen

Eure dritte Anforderung: Daten aus Neo4j holen und vor der Auslieferung
umformen. Das Muster dafür ist in jedem Produkt gleich:

```python
def transform(rows, params) -> list[dict]:      # REIN: kein I/O, kein FastAPI
    ...                                          # ← hier liegt die Fachlichkeit

async def load(repos, params):                   # orchestriert nur
    repo = await repos.materials()
    return transform(await repo.fetch_materials(), params)
```

Die Trennung ist nicht Kosmetik. Die Fachlogik ist der Teil, der Fehler enthält,
und sie muss ohne Server, ohne Datenbank und in Millisekunden testbar sein.
[`tests/test_transformations.py`](../api/tests/test_transformations.py) prüft
genau diese Funktionen — 14 Tests, Laufzeit unter einer Sekunde.

**Womit transformieren?**

* **Reines Python** für einfaches Filtern/Umbenennen (siehe `material_overview_*`).
* **Polars** sobald es aggregiert, verknüpft oder gerechnet wird (siehe
  `supplier_risk_v1`). Dieselbe Bibliothek, die die Dashboards schon benutzen;
  spaltenorientiert und um Größenordnungen schneller als Schleifen über Dicts.

**Wo transformieren?** Faustregel:

```
Filtern/Aggregieren, was die DB gut kann   →  in Cypher/SQL   (weniger Daten übers Netz)
Verknüpfen über Quellgrenzen hinweg        →  in Polars       (die DB kann es nicht)
Fachliche Formeln, Klassifizierung, Runden →  in Polars       (versionierbar, testbar)
```

`supplier_risk_v1` zeigt den mittleren Fall: Lieferantenstammdaten aus Neo4j,
Lieferhistorie aus Postgres, Join und Risiko-Score in Polars. Würde man das im
Dashboard machen, müsste jedes Dashboard beide Verbindungen kennen und die Formel
kopieren — beim ersten Formelwechsel zeigen dann zwei Dashboards zwei Zahlen.

Die Gewichte der Formel stehen bewusst als benannte Konstanten oben in der Datei:
das ist die Stellschraube, über die diskutiert wird.

---

## 8. Antwortformat

Jedes Datenprodukt antwortet gleich — mit einem Umschlag:

```json
{
  "meta": {
    "product": "material-overview",
    "version": "2.0",
    "api_version": "v1",
    "generated_at": "2026-08-20T07:09:05Z",
    "row_count": 64,
    "total_count": 64,
    "source": "neo4j",
    "cache": "hit",
    "deprecated": false,
    "sunset": null
  },
  "data": [ { "material_nr": "MAT-100777", ... } ]
}
```

Warum ein Umschlag statt einer nackten Liste?

* Das Dashboard erfährt, **welche Version** es bekommen hat — unbezahlbar beim
  Debuggen von „bei mir sieht die Tabelle anders aus".
* `generated_at` erlaubt ein ehrliches „Stand: 10:42" in der UI.
* `total_count` ermöglicht serverseitiges Paging (der `DataTable` im Dashboard
  paginiert heute clientseitig; bei großen Neo4j-Ergebnissen wird das eng).
* `source` zeigt, aus welchen Quellen die Antwort zusammengesetzt wurde
  (`neo4j`, `postgres`, `neo4j+postgres`).
* **Metadaten später zu ergänzen ist keine brechende Änderung.** Bei einer nackten
  Liste wäre der Umstieg auf einen Umschlag selbst eine brechende Änderung.

Zusätzlich als HTTP-Header, weil manche Konsumenten den Body nicht auswerten:
`ETag`, `Cache-Control`, `X-Data-Product-Version`, `X-Request-ID`,
und bei veralteten Produkten `Deprecation` / `Sunset`.

---

## 9. Schreibende Endpunkte

Ihr rechnet mit POST/PUT/PATCH. Kurz zu den Methoden — „UPDATE" gibt es in HTTP
nicht, gemeint ist meist PATCH:

| Methode | Bedeutung | Idempotent? |
|---|---|---|
| `POST` | neu anlegen oder Aktion auslösen | nein |
| `PUT` | vollständig ersetzen (ganzer Datensatz) | ja |
| `PATCH` | teilweise ändern (nur gesendete Felder) | ja |
| `DELETE` | löschen | ja |

**Schreibende Endpunkte laufen nicht über die Registry.** Lesen und Schreiben
haben unterschiedliche Verträge:

* Ein **Datenprodukt (GET)** ist ein Vertrag über die *Form* der Daten — cachebar,
  idempotent, versioniert, generierbar.
* Ein **Kommando (POST/PATCH/…)** ist ein Vertrag über eine *Aktion* — mit
  Vorbedingungen, Nebenwirkungen, Berechtigungen und Transaktionen.

Ein Generator kann das Zweite nicht sinnvoll erzeugen. Kommandos sind deshalb
normale, handgeschriebene Router unter `/api/v1/<thema>`
(Beispiel: [`api/v1/mappings.py`](../api/src/data_api/api/v1/mappings.py)).

Zwei Konventionen dort:

1. **Eingabe- und Ausgabemodell trennen** (`MappingIn` / `MappingOut`). Der Client
   darf `id` und `geaendert_am` nicht setzen. Zwei kleine Modelle sind einfacher
   als ein großes mit Ausnahmen.
2. **Nach jedem Schreiben den Cache der betroffenen Produkte invalidieren.** Sonst
   zeigt das Dashboard bis zu `cache_ttl` Sekunden lang den alten Stand, und der
   Nutzer glaubt, das Speichern habe nicht funktioniert:

```python
cache.invalidate("material-overview")
```

---

## 10. Caching und Performance

Dashboards fragen dieselben Daten oft an — jeder Callback, jeder Nutzer, jeder
Reload. Eine Cypher-Aggregation, die 800 ms braucht, darf nicht 40× pro Minute
laufen.

**Drei Ebenen, die zusammenspielen:**

1. **Serverseitiger TTL-Cache** (`products/cache.py`). Schlüssel ist
   `(Produkt, Major, Parameter)` — verschiedene Filter sind verschiedene
   Antworten. Das ist die häufigste Cache-Bug-Quelle überhaupt. TTL ist pro
   Produkt konfigurierbar: 60 s für Stammdaten, 300 s für die teure
   Risikoberechnung, `0` schaltet aus.
2. **ETag + `If-None-Match`.** Beim Polling bekommt das Dashboard `304 Not
   Modified` ohne Body zurück, wenn sich nichts geändert hat. Spart Bandbreite
   und das erneute Rendern großer Tabellen. Der `generated_at`-Zeitstempel bleibt
   bewusst aus dem ETag heraus, sonst änderte er sich bei jedem Request.
3. **Paginierung** (`limit`/`offset`), damit ein Dashboard nicht 2 Mio. Zeilen
   anfragt, um 50 anzuzeigen.

**Bekannte Grenze:** Der Cache liegt im Prozess. Mit mehreren uvicorn-Workern hat
jeder Worker seinen eigenen — die Trefferquote sinkt, die Daten bleiben korrekt.
Für den Anfang völlig in Ordnung. Sobald es stört, tauscht man `TTLCache` gegen
Redis; die Schnittstelle (`get`/`set`/`invalidate`) bleibt gleich.

---

## 11. Fehlerbehandlung

Ein Format für die ganze API — [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457):

```json
{
  "type": "about:blank",
  "title": "Invalid request",
  "status": 422,
  "detail": "Die Anfrageparameter sind ungueltig.",
  "code": "validation_error",
  "request_id": "1fca65e7ef6b",
  "errors": [{"type": "extra_forbidden", "loc": ["query", "stauts"], ...}]
}
```

Warum das zählt: Die Dash-Callbacks brauchen **einen** Pfad für Fehlerbehandlung.
Liefert die API mal `{"detail": ...}`, mal `{"error": ...}` und bei einem
Neo4j-Timeout einen HTML-Stacktrace, steht diese Logik in jedem Dashboard neu.

**Die Konvention im Code:** In der Domänenschicht wird **nie** `HTTPException`
geworfen, sondern eine `AppError`-Unterklasse — die kennt kein HTTP und ist ohne
Webserver testbar. Die Übersetzung nach HTTP passiert an genau einer Stelle
([`core/errors.py`](../api/src/data_api/core/errors.py)).

Die Statuscodes, die für die Dashboards wirklich einen Unterschied machen:

| Code | Bedeutung | Was das Dashboard tut |
|---|---|---|
| `422` | Parameter ungültig | Bug im Callback — beim Entwickeln melden |
| `404` | Produkt/Version gibt es nicht | falsche Version gepinnt |
| `503` | Datenquelle nicht erreichbar | „später erneut versuchen" anzeigen |
| `500` | Bug im Backend | Fehler melden, `request_id` mitschicken |

`503` vs. `500` ist wichtig: Ersteres heißt „nochmal versuchen", Letzteres „bitte
melden". Dafür gibt es `UpstreamUnavailableError`.

Jede Antwort trägt eine `X-Request-ID`, die in jeder Logzeile mitläuft — das ist
die Brücke zwischen „im Dashboard war die Tabelle leer" und der Zeile im Log.

---

## 12. Konfiguration und Secrets

`pydantic-settings` statt `os.getenv()` im Code verstreut:

```python
class Settings(BaseSettings):
    neo4j_uri: str | None = None
    neo4j_auth: str | None = None          # "user/passwort"
    postgres_dsn: str | None = None
    api_cors_origins: list[str] = []
    ...
```

Vorteile: alle Schalter an einer Stelle, typisiert (ein falsch geschriebenes
`NEO4J_UIR` fällt beim Start auf, nicht im ersten Request), Defaults sichtbar
dokumentiert, in Tests austauschbar.

Die Variablennamen sind **absichtlich identisch** zu denen des Dashboards
(`NEO4J_URI`, `NEO4J_AUTH`, `NEO4J_DB`) — dieselbe `.env` funktioniert für beide.

Zwei Dinge, die wichtig werden, sobald es produktiv geht:

* Secrets gehören nicht in die `.env` im Repository, sondern in den
  Secret-Store der Zielplattform (Kubernetes Secret, Vault, Key Vault). Die
  `.env` ist ein Entwicklungswerkzeug; `.env.example` ist das, was eingecheckt wird.
* **CORS ist Pflicht**, weil die Dash-Apps auf einem anderen Port laufen als die
  API. In prod immer explizite Origins — niemals `["*"]` zusammen mit
  Credentials.

---

## 13. Authentifizierung und RBAC

Bewusst minimal gehalten, aber **an der richtigen Stelle verdrahtet**, weil
Nachrüsten sonst teuer wird.

Aktuell: optionaler API-Key im Header `X-API-Key`; ist `API_KEYS` leer, ist die
Prüfung aus (Entwicklung).

Der Punkt ist nicht der API-Key, sondern die **Form**: Auth ist eine
Dependency, die einen `Principal` liefert.

```python
@dataclass(frozen=True)
class Principal:
    subject: str
    groups: frozenset[str]
```

Der Wechsel auf OIDC/JWT (Azure AD o. Ä.) tauscht nur die Implementierung von
`current_principal` aus — kein Router und kein Datenprodukt wird angefasst.

Für euren Anwendungsfall „verschiedenen Gruppen unterschiedliche Dashboards
zeigen" ist der Haken schon gesetzt: ein Datenprodukt darf `required_groups`
deklarieren, der generierte Endpunkt prüft sie:

```python
@data_product(name="supplier-risk", ..., required_groups={"supply-chain"})
```

Der nächste Ausbauschritt (wenn ihr so weit seid) ist **zeilenweise Filterung**:
`principal.groups` fließt in die Cypher-Query, damit ein Nutzer nur die Werke
sieht, für die er berechtigt ist. Das gehört zwingend serverseitig — Ausblenden
im Dashboard ist keine Berechtigung, sondern Kosmetik.

---

## 14. Testing

Drei Ebenen, unterschiedlich schnell und unterschiedlich aussagekräftig:

```
tests/test_transformations.py   Fachlogik pur, ohne DB und HTTP   ← die meisten Tests
tests/test_registry.py          Registry-Regeln und Schutzmechanismen
tests/test_data_products.py     Ende-zu-Ende über HTTP            ← Verdrahtung
tests/test_health.py            Betriebsendpunkte
tests/test_architecture.py      Diagramm-Generator + Veraltungs-Check
```

Zwei Techniken, die das erst möglich machen:

**App-Fabrik statt Modul-Global.** `create_app(settings)` liefert bei jedem
Aufruf eine frische App mit expliziter Konfiguration — kein Monkeypatching, keine
Umgebungsvariablen im Test.

**`dependency_overrides`.** FastAPIs eingebauter Mechanismus, um in Tests eine
Dependency zu ersetzen:

```python
app.dependency_overrides[get_repositories] = lambda: FakeRepositories()
```

> Beim Bauen ist genau hier ein echter Bug aufgefallen: `/readyz` las die
> Settings über `Depends(get_settings)` — und das ist `lru_cache`d, las also
> weiter aus der Umgebung statt der an `create_app()` übergebenen Konfiguration.
> Der Test „prod ohne Datenbank meldet sich nicht bereit" schlug deshalb fehl.
> Behoben in `create_app()` durch
> `app.dependency_overrides[get_settings] = lambda: settings`.

**Test-Doubles statt Ersatzpfad im Produktionscode.** Die Beispieldaten liegen in
[`tests/fakes.py`](../api/tests/fakes.py) und werden über
`dependency_overrides[get_repositories]` eingehängt. Ersetzt wird nur die
unterste Schicht — Route, Validierung, Produkt-Loader, Transformation, Umschlag,
Cache und Header laufen unverändert.

Der Unterschied zu Beispieldaten in `src/` ist nicht kosmetisch:

| | Beispieldaten in `src/` | Test-Doubles in `tests/` |
|---|---|---|
| Wird ausgeliefert | ja — kann in Produktion aktiv werden | nein |
| Braucht ein Sicherheitsnetz | ja (Schalter, Readiness-Sonderfall) | nein |
| Lebensdauer | muss später ausgebaut werden | bleibt dauerhaft nützlich |

Deshalb laufen alle Tests ohne Datenbank und ohne Docker — die Voraussetzung für
sinnvolle CI.

---

## 15. Betrieb und Deployment

```bash
uvicorn data_api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

* **Worker-Anzahl**: Jeder Worker ist ein eigener Prozess mit eigenem
  Neo4j-Treiber, eigenem SQL-Pool und eigenem Cache. Bei der Dimensionierung der
  Datenbank-Pools berücksichtigen: `pool_size × workers`.
* **`/healthz` vs. `/readyz`**: Liveness prüft **nichts** Externes — sonst killt
  ein kurzer Neo4j-Ausfall alle Pods, statt nur Fehler zu liefern. Readiness
  prüft die Datenquellen und nimmt den Pod bei Bedarf aus dem Loadbalancer.
* **Logging** geht nach stdout (Container-Konvention), mit Request-ID in jeder
  Zeile.
* **OpenAPI** unter `/docs` (interaktiv), `/redoc` (lesbar), `/openapi.json`
  (maschinell). Letzteres kann in CI gegen die vorige Version geprüft werden, um
  unbeabsichtigt brechende Änderungen zu finden.

---

## 16. Anbindung der Dashboards

**Umgesetzt** für das Material-Management-Dashboard. Im Dashboard sind Cypher,
Neo4j-Treiber und Zugangsdaten verschwunden:

```
vorher:   Dashboard ──Bolt/Cypher──► Neo4j
nachher:  Dashboard ──HTTP/JSON────► API-Layer ──► Neo4j
```

| Datei | Vorher | Nachher |
|---|---|---|
| `data/neo4j.py` | Treiber, Zugangsdaten | gelöscht |
| `data/repository.py` | Cypher + Mock-Daten | ruft den API-Client |
| `data/api_client.py` | — | neu, kennt HTTP |
| `app.py` | legt den Treiber an | nichts mehr davon |
| `requirements.txt` | `neo4j` | `httpx` |

**Filter, KPIs, Tabelle und Callbacks wurden nicht angefasst** — sie kannten
schon vorher nur `get_materials()`. Das war der Verdienst der bestehenden
Isolation der Datenschicht und die eigentliche Probe darauf, ob der Vertrag trägt.

### Die Grenze zwischen API-Vertrag und Tabellenspalten

Beides ist nicht dasselbe. Die API liefert `werk_id` und `werk_name`, die Tabelle
hat historisch eine Spalte `werk`. Übersetzt wird an einer sichtbaren Stelle:

```python
# data/repository.py
_API_TO_UI = {
    "werk_name": "werk",      # die einzige echte Umbenennung
    ...                       # werk_id und preis fehlen: nicht gebraucht
}
```

Zwei Effekte: Beim Umbau musste keine Filter- oder Callback-Datei angefasst
werden. Und **ein neues Feld in der API kann das Dashboard nie brechen**, weil
nur übernommen wird, was in dieser Abbildung steht — der Grund, warum ein
hinzugefügtes Feld nur MINOR ist.

### Der Client ist eine Kopiervorlage

[`clients/dash_client.py`](../api/src/data_api/clients/dash_client.py) wird in
jedes Dashboard kopiert, nicht importiert: `api/` hängt an FastAPI, dem
Neo4j-Treiber und SQLAlchemy — nichts davon soll ins Dashboard, das nur `httpx`
braucht. Sobald das dritte Dashboard ihn nutzt, lohnt sich ein kleines
gemeinsames Paket. Der Client ist generisch (er kennt kein Feld eines
Datenprodukts), die Kopien driften also nicht fachlich auseinander.

Er ist bewusst **synchron** (`httpx.Client`) — Dash-Callbacks sind synchron, ein
`asyncio.run()` darin wäre ein Fehler mit Ansage.

### Was bewusst noch offen ist

Das Dashboard holt weiterhin die volle Tabelle und filtert lokal in Polars. Das
war der kleinstmögliche Umbau. Sobald die echten Datenmengen bekannt sind, lohnt
der zweite Schritt: die Filter als Query-Parameter mitschicken
(`?status=Gesperrt&werk_id=W-KOE`). Die Parametermodelle dafür existieren bereits
(`MaterialParamsV2`), und `meta.total_count` liefert die Gesamtzahl für echtes
serverseitiges Paging.

## 17. Ein neues Datenprodukt anlegen

Der Test, ob das Konzept „leicht erweiterbar" hält. Eine Datei in
`products/catalog/`, sonst nichts:

```python
# products/catalog/werk_auslastung_v1.py
from pydantic import BaseModel
from data_api.db.repositories import Repositories
from data_api.products.base import ProductParams
from data_api.products.registry import data_product


class WerkRow(BaseModel):              # 1. der Vertrag
    werk_id: str
    werk_name: str | None = None
    materialien: int
    bestand_gesamt: int


class WerkParams(ProductParams):       # 2. die erlaubten Filter
    min_materialien: int = 0


def transform(rows, params):           # 3. die Fachlichkeit — rein, testbar
    nach_werk: dict[str, dict] = {}
    for row in rows:
        eintrag = nach_werk.setdefault(row["werk_id"], {
            "werk_id": row["werk_id"], "werk_name": row["werk_name"],
            "materialien": 0, "bestand_gesamt": 0,
        })
        eintrag["materialien"] += 1
        eintrag["bestand_gesamt"] += row.get("bestand") or 0
    return [w for w in nach_werk.values() if w["materialien"] >= params.min_materialien]


@data_product(                          # 4. anmelden
    name="werk-auslastung", version="1.0",
    summary="Materialien und Bestand je Werk",
    item_model=WerkRow, params_model=WerkParams,
    owner="team-material-management", cache_ttl=120,
)
async def load(repos: Repositories, params: WerkParams):
    """Aggregierte Kennzahlen je Werk."""
    repo = await repos.materials()
    return transform(await repo.fetch_materials(), params)
```

Nach dem Neustart existiert automatisch:

* die Route `GET /api/v1/data-products/werk-auslastung/v1`
* der Alias `/latest`
* der vollständige OpenAPI-Eintrag mit Schema unter `/docs`
* der Katalogeintrag unter `/api/v1/catalog`
* Caching, ETag, Paginierung, Fehlerformat, Auth-Prüfung

Kein Router, keine `main.py`, keine Registrierungsliste wurde angefasst.

---

## 18. Automatische Architekturdokumentation

Ein Diagramm, das von Hand gepflegt wird, ist nach drei Sprints falsch — und ein
falsches Diagramm ist schlimmer als keines. Deshalb wird die visuelle
Dokumentation **aus der laufenden App erzeugt**:

```bash
architecture-docs            # schreibt docs/architecture.md
architecture-docs --check    # CI: schlägt fehl, wenn die Datei veraltet ist
```

Ergebnis: [`docs/architecture.md`](architecture.md) mit drei Mermaid-Diagrammen
(Datenfluss Route → Produkt → Repository → Quelle, Versionsstände,
Vertragsschemata), einem Routeninventar und einem Steckbrief je Datenprodukt.

### Warum selbst gebaut statt eines fertigen Pakets

Es gibt brauchbare Werkzeuge, aber keines passt auf diese Architektur:

| Werkzeug | Was es tut | Warum es hier zu kurz greift |
|---|---|---|
| [`fastapi-router-viz`](https://pypi.org/project/fastapi-router-viz/) | Lädt die App, zeichnet Routen → Pydantic-Schemata → Module (DOT/PNG/Webansicht) | Kommt den Routen nahe, endet aber beim Schema. Repositories, Datenquellen, Versionen, Owner, Cache kennt es nicht. Braucht Graphviz, kein Mermaid. |
| [`fastapi-di-viz`](https://pypi.org/project/fastapi-di-viz/) | Läuft den Dependency-Baum ab, gibt DOT **und Mermaid** aus | Bei uns hängt *jede* Route an derselben Dependency (`get_repositories`). Der Graph sähe für alle Routen gleich aus. |
| [`pyreverse`](https://pylint.readthedocs.io/en/stable/additional_tools/pyreverse/index.html) (in pylint) | UML-Klassendiagramme, u. a. als `.mmd` | Gut für Klassenbeziehungen, kennt aber keine Routen und keinen Datenfluss. |
| `pydeps`, `code2flow` | Modul- bzw. Aufrufgraphen | Zeigen Dateien, nicht Fachlichkeit. Bei ~35 Modulen entsteht ein unlesbarer Teller Spaghetti. |

Der entscheidende Punkt ist ein Detail unserer Architektur: **Unsere
Datenprodukt-Routen existieren nicht im Quelltext.** Sie entstehen zur Laufzeit
aus der Registry. Jedes Werkzeug, das Quelltext parst, sieht sie schlicht nicht.

Umgekehrt weiß die Registry bereits alles, was ein generisches Werkzeug mühsam
erraten müsste: Version, Owner, Cache-TTL, Deprecation-Status, Vertragsfelder,
erlaubte Filter. Diese Information *nicht* zu nutzen wäre die eigentliche
Verschwendung. Der Generator ist deshalb rund 200 Zeilen
([`architecture.py`](../api/src/data_api/architecture.py)) und liefert ein
genaueres Bild als jedes der Pakete oben.

### Wie die Verbindungen ermittelt werden

Drei Quellen, alle abgeleitet — nichts wird von Hand gepflegt:

| Information | Woher |
|---|---|
| Routen, Methoden, Tags, Deprecation | `app.openapi()` — der öffentliche, stabile Vertrag der App |
| Produkt, Version, Owner, Cache, Vertragsfelder | die Registry |
| **Produkt → Repository** | AST des Loaders: welche `repos.X()` ruft er auf |
| **Repository → Datenquelle** | AST des `Repositories`-Containers: welche Adapter gibt er zurück, plus deren `source`-Attribut |

Die letzten beiden sind der Trick. `repositories_used_by()` parst den Loader und
sammelt alle Attributaufrufe auf dessen erstem Parameter — per AST und nicht per
Regex, damit ein `repos.materials` im Kommentar nicht mitzählt.

> **Nebenbefund beim Bauen:** Der erste Entwurf las die Routen aus `app.routes`.
> In der installierten FastAPI-Version liegen eingebundene Router aber als
> interne `_IncludedRouter`-Objekte vor, nicht flach ausgerollt — das Diagramm
> kam mit *null* Routen heraus. Das OpenAPI-Schema ist die stabilere Quelle und
> gleichzeitig genau das, was FastAPI offiziell garantiert.

### Der Teil, der es am Leben hält

```python
def test_dokumentation_ist_aktuell():
    assert DEFAULT_OUT.read_text() == build()
```

Wer ein Datenprodukt anlegt und die Doku nicht neu erzeugt, bekommt einen roten
Build statt eines stillschweigend falschen Diagramms. Das ist der eigentliche
Wert der Automatisierung — nicht das Zeichnen, sondern die garantierte
Aktualität.

Optional prüft [`api/tools/validate_mermaid.mjs`](../api/tools/validate_mermaid.mjs)
die erzeugten Diagramme mit mermaids echtem Parser. Ohne das fällt ein
Syntaxfehler erst auf, wenn jemand die Datei öffnet — und dort steht dann nur
„Syntax error in text" statt eines Diagramms.

### Was zusätzlich sinnvoll ist

* **[`import-linter`](https://import-linter.readthedocs.io/)** — die
  *Durchsetzung* zum Diagramm. Man deklariert die Schichtenordnung als Vertrag,
  und der Build schlägt fehl, sobald ein Repository FastAPI importiert oder eine
  `transform()`-Funktion eine Datenbank anfasst:

  ```ini
  [importlinter:contract:schichten]
  name = Abhängigkeiten zeigen nur nach unten
  type = layers
  layers =
      data_api.api
      data_api.products
      data_api.repositories
      data_api.db
      data_api.core
  ```

  Das Diagramm zeigt, wie es *ist*; der Contract erzwingt, wie es *sein soll*.
* **OpenAPI-Diff in CI** — `/openapi.json` gegen den Stand des Zielbranches
  prüfen, um unbeabsichtigt brechende Änderungen an Datenprodukten zu finden,
  bevor sie ein Dashboard treffen.
* **MkDocs**, sobald die Dokumentation wächst: rendert Mermaid nativ und kann
  `docs/` als durchsuchbare interne Seite ausliefern.

---

## 19. Roadmap und offene Entscheidungen

### Was jetzt steht

- ✅ Datenprodukt-Registry mit typisierten, generierten Routen
- ✅ Zweiachsige Versionierung inkl. Deprecation-/Sunset-Mechanik
- ✅ Neo4j- und Postgres-Lebenszyklus, Session-Management pro Request
- ✅ Test-Doubles + Seed-Skripte — Tests ohne Datenbank, Mock-Daten in der DB
- ✅ Cross-Source-Produkt mit Polars-Transformation als Referenz
- ✅ Cache, ETag, Paginierung, Problem Details, Request-IDs, Health/Readiness
- ✅ Client-Vorlage für die Dash-Apps
- ✅ Automatisch erzeugte Architekturdiagramme mit Veraltungs-Check in CI
- ✅ 47 Tests

### Nächste Schritte, in dieser Reihenfolge

1. **Erste echte Quelle anbinden.** Sobald Neo4j steht: `NEO4J_URI` setzen,
   Cypher in `repositories/materials.py` an das echte Graphmodell anpassen. Alles
   andere bleibt. Danach `meta.source` prüfen — steht dort `neo4j`, ist die
   Umstellung durch.
2. **Ein Dashboard umstellen.** Material Management auf den Client umbauen; das
   ist die Probe, ob der Vertrag trägt.
3. **Auth aktivieren**, sobald klar ist, welcher Identity-Provider genutzt wird.
   API-Keys als Zwischenlösung reichen für den internen Betrieb.
4. **Alembic aufsetzen** (`migrations/`), sobald die erste schreibende
   Postgres-Tabelle existiert.
5. **Filter serverseitig** ziehen, wenn die echten Datenmengen bekannt sind.

### Offene Entscheidungen für euch als Team

| Frage | Optionen | Meine Empfehlung |
|---|---|---|
| **Ein API-Service oder mehrere?** | Monolith / Service je Domäne | Ein Service. Bei drei Dashboards und einem Team ist alles andere Overhead. Der Schnitt nach Datenprodukten macht ein späteres Aufteilen einfach. |
| **Wer schreibt Datenprodukte?** | zentrales API-Team / die Dashboard-Teams selbst | Die Dashboard-Teams, mit `owner`-Pflichtfeld und Review. Ein zentrales Team wird sonst zum Flaschenhals. |
| **Wie streng ist die Versionsregel?** | strikt / pragmatisch | Strikt ab dem ersten produktiven Dashboard. Vorher darf man v1 noch ändern — dann nie wieder. |
| **Cache im Prozess oder Redis?** | TTLCache / Redis | Erst mal im Prozess. Redis, wenn ihr über zwei Worker hinausgeht oder die Trefferquote messbar leidet. |
| **Antwortformat: Records oder spaltenweise?** | `[{...}]` / `{"columns": [], "rows": [[]]}` | Records. Spaltenweise ist bei >100k Zeilen deutlich kompakter — dann als zusätzlicher `format`-Parameter, nicht als Ersatz. |
| **Wo liegt der `dash_client`?** | kopieren / gemeinsames Paket | Kurzfristig kopieren. Sobald das dritte Dashboard ihn nutzt, ein kleines internes Paket bauen. |

### Was bewusst *nicht* drin ist

* **Kein Repository-übergreifendes ORM für die Leseseite.** Datenprodukte lesen
  aggregiert, sie mappen keine Entitäten. ORM-Modelle lohnen sich auf der
  Schreibseite.
* **Kein GraphQL.** Klingt bei einer Graphdatenbank naheliegend, löst aber ein
  Problem, das ihr nicht habt (viele unbekannte Clients mit unbekannten
  Abfragemustern), und macht Caching und Versionierung deutlich schwerer.
* **Kein generischer Cypher-Endpunkt.** Siehe Abschnitt 1.
* **Kein Websocket/Streaming.** Erst wenn ein Dashboard echte Live-Daten braucht;
  Polling mit ETag reicht für Minutenaktualität.
