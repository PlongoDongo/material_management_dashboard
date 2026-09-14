# Lokal testen — Schritt für Schritt

Diese Anleitung geht von „nichts läuft" bis „Datenprodukt aus der echten
Datenbank" in kleinen Stufen. Jede Stufe hat **ein** Erfolgskriterium. Wenn eine
fehlschlägt, weißt du damit genau, wo das Problem liegt — statt am Ende vor
einem Server zu stehen, der „irgendwie nicht geht".

Alle Befehle laufen im Ordner `api/`.

> **zsh-Fallstrick:** URLs mit `?` oder `&` **immer in Anführungszeichen**.
> Ohne sie versucht zsh, das `?` als Datei-Muster aufzulösen, und meldet
> `no matches found`. Das sieht wie ein Server-Fehler aus, ist aber die Shell.
> ```bash
> curl "http://127.0.0.1:8000/api/v1/data-products/material-overview/v3?limit=2"
> ```

---

## Stufe 0 — Umgebung

```bash
cd api
uv venv                                  # oder: python -m venv .venv
uv pip install -e ".[dev]"               # oder: .venv/bin/pip install -e ".[dev]"
```

**Erfolg:** Der Befehl läuft ohne Fehler durch.

```bash
.venv/bin/python -c "from core.config import __version__; print(__version__)"
```

Gibt `0.1.0` aus. Damit ist der Code importierbar — das allein schließt schon
die häufigsten Einrichtungsprobleme aus.

---

## Stufe 1 — Konfiguration prüfen, ohne etwas zu starten

Der schnellste Weg herauszufinden, **was die Anwendung tatsächlich sieht**:

```bash
.venv/bin/python -m core.config
```

Ausgabe (gekürzt):

```
credentials dir: /etc/credentials
api_env='dev' server_host='127.0.0.1' server_port=8000 server_loglevel='info'
neo4j_host=None neo4j_password=None sql_host=None ...
```

**Erfolg:** Der Befehl läuft und zeigt die Werte. Passwörter erscheinen als
`SecretStr('**********')` — das ist beabsichtigt, nicht kaputt.

Worauf du achtest:

| Zeile | Bedeutung |
|---|---|
| `credentials dir:` | Wo gesucht wird. Stimmt der Pfad? |
| `neo4j_host=None` | Es wurde **nichts** gefunden — lokal normal |
| `neo4j_host='...'` | Die Credentials-Datei wurde gelesen |

Dieser Schritt braucht **keine** laufende Datenbank und **keinen** Server. Wenn
hier etwas nicht stimmt, brauchst du gar nicht weiterzumachen.

---

## Stufe 2 — Server starten, ganz ohne Datenbank

Genau das soll funktionieren: Die API startet auch, wenn keine Datenquelle
erreichbar ist, und meldet die Quellen als inaktiv.

```bash
.venv/bin/python -m main
```

Erwartete Ausgabe:

```
INFO     Data product catalog loaded: 4 products.
INFO     Data product routes created: 4 products.
INFO:    Started server process [55303]
WARNING  Neo4j host is not configured -- Neo4j inactive.
WARNING  Postgres host is not configured -- SQL inactive.
INFO     Ready: 4 data products, env=dev
INFO:    Application startup complete.
INFO:    Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

**Erfolg:** `Application startup complete.`

Die beiden `WARNING`-Zeilen sind hier **richtig**, nicht das Problem.

> Dass „catalog loaded" zweimal erscheint, ist normal: `python -m main`
> lädt das Modul einmal als `__main__`, und uvicorn importiert es danach über
> den Namen `main` erneut. Nur die zweite Instanz wird bedient.

Host, Port und Log-Level kommen aus der Konfiguration, lassen sich also ohne
Code-Änderung umstellen:

```bash
SERVER_PORT=8080 SERVER_LOGLEVEL=debug .venv/bin/python -m main
```

### „Address already in use" (Errno 98 / 48)

```
INFO:     Will watch for changes in these directories: [...]
ERROR:    [Errno 98] Address already in use
```

Der Port ist belegt. Dass im Browser unter `http://127.0.0.1:8000` nichts
antwortet, beweist das Gegenteil **nicht** — der Halter kann auf einer anderen
Schnittstelle lauschen, auf HTTP gar nicht antworten, oder eine Anwendung sein,
die selbst nicht hochgekommen ist. Frag das Betriebssystem statt den Browser:

```bash
ss -ltnp 'sport = :8000'          # Linux
lsof -nP -iTCP:8000 -sTCP:LISTEN  # macOS, oder Linux mit lsof
fuser -v 8000/tcp                 # Linux, kurz und knapp
```

Die Ausgabe nennt PID und Kommando. **Zwei Ursachen decken fast alle Fälle ab:**

**1. Ein vergessener Server aus einem anderen Projekt.** Der häufigste Fall, und
der am schwersten zu erratende — `8000` ist der Default von so ziemlich jedem
Python-Webframework. Achte in der `NAME`-Spalte auf den Unterschied:

```
python  15393  *:8000            <- 0.0.0.0, ALLE Schnittstellen
python  89468  127.0.0.1:8000    <- nur lokal
```

Wer `0.0.0.0:8000` hält, blockiert `127.0.0.1:8000` **mit**. Auf Linux gibt das
`Errno 98`; auf macOS geht es teilweise durch, weshalb derselbe Fehler dort
womöglich nicht auftritt.

**2. Ein verwaistes Kind des Reloaders.** `reload` (an, solange `API_ENV=dev`)
startet zwei Prozesse: einen Beobachter und ein Kind, das den Socket hält. Wird
der Beobachter hart beendet — geschlossenes Terminal, `kill -9`, abgestürzte
IDE — überlebt das Kind und behält den Port. Nachgestellt:

```
Elternprozess mit kill -9 beendet
  -> PID 89664 haelt 127.0.0.1:8877 weiterhin
```

Ein `Ctrl-C` im Vordergrund räumt beide sauber ab; alles Härtere nicht.

**Beheben** — je nachdem, was der Halter ist:

```bash
kill <PID>                        # freundlich; -9 nur wenn das nicht reicht
pkill -f "python -m main"         # alle eigenen Instanzen
SERVER_PORT=8001 .venv/bin/python -m main    # oder einfach ausweichen
```

Prüf vor dem Killen die Kommandozeile in der `ss`/`lsof`-Ausgabe. Ein
`0.0.0.0:8000` aus einem fremden Projektpfad gehört einem Kollegen oder einem
anderen Container — dann ist Ausweichen auf einen anderen Port die richtige
Antwort, nicht das Beenden.

---

## Stufe 3 — Der einfachste Endpunkt

Zweites Terminal:

```bash
curl -s http://127.0.0.1:8000/api/v1/healthz | python -m json.tool
```

```json
{
    "status": "ok",
    "version": "0.1.0",
    "data_products": 4
}
```

**Erfolg:** HTTP 200 mit diesem Inhalt.

`/healthz` prüft **absichtlich nichts Externes** — es beantwortet nur „der
Prozess lebt". Deshalb funktioniert es auch ohne Datenbank.

---

## Stufe 4 — Was sagt die API über ihre Datenquellen?

```bash
curl -s -w "\nHTTP %{http_code}\n" http://127.0.0.1:8000/api/v1/readyz | python -m json.tool
```

Ohne Datenbank:

```json
{
    "status": "degraded",
    "env": "dev",
    "required": ["neo4j", "postgres"],
    "checks": {
        "neo4j": "not-configured",
        "postgres": "not-configured"
    }
}
```
→ **HTTP 503**

**Erfolg:** 503 mit `not-configured`. Das ist an dieser Stelle die *richtige*
Antwort — `/readyz` sagt „ich kann noch keine Anfragen beantworten".

Dieser Endpunkt ist ab jetzt dein wichtigstes Werkzeug: Er sagt dir pro
Datenquelle, woran es liegt.

| `checks`-Wert | Bedeutung |
|---|---|
| `not-configured` | Keine Zugangsdaten gefunden → Stufe 1 |
| `unreachable` | Zugangsdaten da, Verbindung scheitert → Netz/Firewall |
| `ok` | Alles gut |

---

## Stufe 5 — Katalog: welche Datenprodukte gibt es?

Authentifizierung ist lokal aus (kein `OIDC_ISSUER`), also braucht es keinen Token.

```bash
curl -s http://127.0.0.1:8000/api/v1/catalog | python -m json.tool
```

Kurzfassung:

```
material-overview    latest=3.0   versionen=['2.1', '3.0']
material-search      latest=1.0   versionen=['1.0']
supplier-risk        latest=2.0   versionen=['2.0']
```

**Erfolg:** HTTP 200 mit drei Produkten.

Das funktioniert **ohne Datenbank**, weil der Katalog nur die Registry liest.
Damit hast du bewiesen: Routing, Serialisierung und die Produktregistrierung
sind in Ordnung — bevor überhaupt eine Datenbank im Spiel ist.

Im Browser gibt es dasselbe schöner:

```
http://127.0.0.1:8000/docs
```

---

## Stufe 6 — Ein Datenprodukt, noch ohne Datenbank

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  "http://127.0.0.1:8000/api/v1/data-products/material-overview/v3?limit=2" | python -m json.tool
```

```json
{
    "type": "about:blank",
    "title": "Server misconfigured",
    "status": 500,
    "detail": "Neo4j is not configured (NEO4J_HOST is missing) but is required here.",
    "code": "configuration_error",
    "request_id": "080455969de7"
}
```

**Erfolg:** HTTP 500 mit `configuration_error`.

Das ist kein Rückschritt, sondern das erwartete Ergebnis: Die Route existiert,
die Parameter wurden validiert, der Loader lief los und ist am fehlenden Neo4j
gescheitert — mit einer Meldung, die sagt, welche Einstellung fehlt.

Zwei nützliche Nebenprüfungen ohne Datenbank:

```bash
# Tippfehler-Schutz: ein unbekannter Parameter ist ein Fehler, keine stille Ignoranz
curl -s "http://127.0.0.1:8000/api/v1/data-products/material-overview/v3?stauts=Aktiv" \
  | python -c "import json,sys; b=json.load(sys.stdin); print(b['code'], b['errors'][0]['msg'])"
# -> validation_error Extra inputs are not permitted

# Antwort-Header
curl -s -D - -o /dev/null "http://127.0.0.1:8000/api/v1/healthz" | grep -i "^x-"
# -> x-request-id: 699db4a4bef9
#    x-response-time-ms: 1.6
```

Die `x-request-id` ist die Nummer, mit der du eine Antwort in den Server-Logs
wiederfindest — sie steht auch im Fehler-Body.

---

## Stufe 7 — Jetzt mit den echten Zugangsdaten

Erst **ohne** Server prüfen, ob die Datei gefunden und richtig gelesen wird:

```bash
.venv/bin/python -m core.config
```

```
credentials dir: /etc/credentials
... neo4j_host='neo4j.intern' neo4j_port=7687 neo4j_username='neo4j'
    neo4j_password=SecretStr('**********') sql_host='pg.intern' ...
```

**Erfolg:** Die Felder sind gefüllt statt `None`.

Falls nicht:

| Symptom | Ursache |
|---|---|
| alles `None`, `credentials dir` stimmt | Dateiname passt nicht — erwartet werden genau die Namen aus `_CREDENTIAL_FILES` in `core/config.py` (aktuell `neo4j.dev` und `postgres.project`) |
| einzelne Felder `None` | Schlüsselnamen weichen ab → `_CREDENTIAL_FIELDS` in `core/config.py` anpassen |
| `ConfigurationError: ... is unreadable` | Datei da, aber weder YAML noch JSON |
| `ConfigurationError: ... key/value pairs` | Datei ist z.B. `KEY=value` statt `key: value` |
| `ValidationError: neo4j_port` | Wert hat den falschen Typ |

### Format der Datei prüfen, ohne das Passwort zu sehen

Die Dateiendung sagt nichts über den Inhalt — `neo4j.dev` wird genauso gelesen
wie `neo4j.yaml`. Gelesen wird mit `yaml.safe_load`, das **YAML und JSON**
versteht (JSON ist eine Teilmenge von YAML). Wenn du wissen willst, was
tatsächlich drinsteht, ohne die Werte auf den Bildschirm zu holen:

```bash
.venv/bin/python -c "
import yaml, sys
inhalt = yaml.safe_load(open(sys.argv[1]))
print(type(inhalt).__name__, sorted(inhalt) if isinstance(inhalt, dict) else repr(inhalt)[:60])
" /etc/credentials/neo4j.dev
```

Ausgegeben werden nur **Typ und Schlüsselnamen**, keine Werte:

```
dict ['host', 'password', 'port', 'protocol', 'username']
```

| Ausgabe | Bedeutung |
|---|---|
| `dict [...]` | passt — vergleiche die Schlüssel mit `_CREDENTIAL_FILES` in `core/config.py` |
| `str '...'` | vermutlich `KEY=value`-Format, kein YAML/JSON — sag Bescheid, dann bauen wir einen Parser dafür |
| `ParserError` | etwas ganz anderes (INI, XML) |

Der Pfad lässt sich zum Ausprobieren umbiegen:

```bash
CREDENTIALS_DIR=/tmp/meine-creds .venv/bin/python -m core.config
```

Und einzelne Werte lassen sich per Umgebungsvariable übersteuern — die haben
Vorrang vor der Datei:

```bash
NEO4J_HOST=localhost .venv/bin/python -m core.config
```

### Dann den Server starten

```bash
.venv/bin/python -m main
```

**Erfolg:**

```
INFO     Connected to Neo4j: bolt://neo4j.intern:7687
INFO     SQL engine created.
INFO     Ready: 4 data products, env=dev
INFO:    Application startup complete.
```

**Wenn stattdessen das hier kommt:**

```
neo4j.exceptions.ServiceUnavailable: Failed to DNS resolve address
  nicht-erreichbar.invalid:7687: [Errno 8] nodename nor servname provided
ERROR:   Application startup failed. Exiting.
```

dann ist die **Konfiguration richtig und das Netz das Problem** — die URI wurde
korrekt zusammengebaut, der Treiber kommt nur nicht hin. VPN, Firewall,
Hostname. Das ist eine gute Nachricht: Stufe 1–6 sind bestanden.

Der Startabbruch ist Absicht: Lieber fällt der Container sofort um, als „healthy"
zu melden und jede Anfrage scheitern zu lassen.

### Verbindung prüfen

```bash
curl -s http://127.0.0.1:8000/api/v1/readyz | python -m json.tool
# -> "status": "ok", checks: {"neo4j": "ok", "postgres": "ok"}
```

---

## Stufe 8 — Echte Daten

```bash
curl -s "http://127.0.0.1:8000/api/v1/data-products/material-overview/v3?limit=3" \
  | python -m json.tool
```

Erwartet: ein Umschlag mit `meta` und `data`.

```json
{
  "meta": {
    "product": "material-overview", "version": "3.0",
    "generated_at": "...", "row_count": 3, "total_count": 64,
    "source": "neo4j", "cache": "miss"
  },
  "data": [ { "material_number": "MAT-100777", ... } ]
}
```

**Erfolg:** HTTP 200, `row_count` = 3, `source` = `neo4j`.

Was du an `meta` ablesen kannst:

- `cache: "miss"` beim ersten, `"hit"` beim zweiten Aufruf → der Cache arbeitet
- `total_count` > `row_count` → es gibt mehr Zeilen als geholt
- `source` → welche Datenquelle tatsächlich gefragt wurde

Dann in der Komplexität weiter:

```bash
# Filter
curl -s ".../data-products/material-overview/v3?limit=5&status=Gesperrt" | python -m json.tool

# Serverseitige Paginierung (material-search filtert IN der Query)
curl -s ".../data-products/material-search/v1?limit=3&offset=0"
curl -s ".../data-products/material-search/v1?limit=3&offset=3"
# -> unterschiedliche Zeilen, gleicher total_count

# Zwei Quellen (Neo4j + Postgres): braucht auch Postgres
curl -s ".../data-products/supplier-risk/v2?limit=5" | python -m json.tool
```

> Ohne Testdaten im Graphen kommt eine leere `data`-Liste zurück — das ist kein
> Fehler. `api/seed/seed_neo4j.py` legt einen Beispielbestand an.

---

## Stufe 9 — Die Architektur-Doku erzeugen

```bash
.venv/bin/architecture-docs
```

```
Data product routes created: 4 products.
/Users/.../docs/architecture.md written.
```

**Erfolg:** Die Datei wurde geschrieben.

Sie enthält ein Mermaid-Diagramm und drei Tabellen:

| Abschnitt | Inhalt |
|---|---|
| `## Contracts` | Diagramm: die Felder je Produktversion |
| `## Write routes` | schreibende Routen: Rolle, Ziel-Datenquelle, invalidierte Produkte |
| `## Route inventory` | jede Route mit Produkt, Version, Owner, Cache, Status, Sunset |
| `## Data products in detail` | Steckbrief je Produkt, u. a. welche Datenquelle es braucht |

Ansehen kannst du sie in jedem Markdown-Viewer mit Mermaid-Unterstützung —
GitHub rendert sie direkt, in VS Code über die Vorschau.

Die Datei wird **aus der laufenden App** erzeugt, nicht gepflegt. Deshalb
gibt es auch:

```bash
.venv/bin/architecture-docs --check     # nur prüfen, ob es aktuell ist (für CI)
.venv/bin/architecture-docs --out /tmp/architektur.md
```

Eine Datenbank braucht es dafür nicht — die Struktur steht in der Registry.

---

## Wenn du fertig bist: die Tests

```bash
.venv/bin/python -m pytest -q          # 135 grün, 7 übersprungen (brauchen Neo4j)
.venv/bin/ruff check src tests
```

Die übersprungenen laufen mit:

```bash
export NEO4J_HOST=localhost NEO4J_USER=neo4j NEO4J_PASSWORD=password
python seed/seed_neo4j.py
.venv/bin/python -m pytest tests/test_integration_neo4j.py -v
```

---

## Kurzreferenz

| Stufe | Befehl | Erfolg |
|---|---|---|
| 0 | `uv pip install -e ".[dev]"` | läuft durch |
| 1 | `python -m core.config` | zeigt die Werte |
| 2 | `python -m main` | `Application startup complete.` |
| 3 | `curl .../api/v1/healthz` | `{"status": "ok"}` |
| 4 | `curl .../api/v1/readyz` | 503 `not-configured` bzw. 200 `ok` |
| 5 | `curl .../api/v1/catalog` | drei Produkte |
| 6 | `curl ".../material-overview/v3?limit=2"` | 500 ohne DB, 200 mit |
| 7 | `python -m core.config` | Felder gefüllt statt `None` |
| 8 | `curl ".../material-overview/v3?limit=3"` | `meta` + `data` |
| 9 | `architecture-docs` | `architecture.md written.` |
