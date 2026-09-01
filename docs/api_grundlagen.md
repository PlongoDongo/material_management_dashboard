# API-Grundlagen: warum das alles so aussieht

Diese Datei erklärt die Ideen hinter dem API-Layer **von Grund auf** — ohne
vorauszusetzen, dass du FastAPI, async, Session-Management oder Caching schon
kennst.

Der Aufbau folgt einer Beobachtung, die du selbst formuliert hast: Fast jeder
heutige Standard in der Softwarearchitektur ist die Antwort auf einen konkreten
Schmerz, den jemand vorher hatte. Wenn man nur das Endergebnis sieht, wirkt es
überkompliziert. Wenn man die Reihe *Ansatz → Problem → Gegenmittel → neues
Problem* kennt, wirkt es meist ziemlich zwingend.

Deshalb geht dieses Dokument die Geschichte durch und zeigt am Ende, welche
Zeile in unserem Code welchem historischen Schmerz entspricht.

> Die Jahreszahlen sind grobe Orientierung, keine exakte Chronologie —
> Übergänge dauerten oft Jahre und liefen parallel.

---

## Inhalt

* [Teil 1 — Was ein API-Layer eigentlich ist](#teil-1--was-ein-api-layer-eigentlich-ist)
* [Teil 2 — Die Geschichte in acht Schritten](#teil-2--die-geschichte-in-acht-schritten)
* [Teil 3 — Vier Nebenstränge](#teil-3--vier-nebenstränge)
* [Teil 4 — Ein Request durch unseren Code](#teil-4--ein-request-durch-unseren-code)
* [Teil 5 — Vier Bausteine im Detail](#teil-5--vier-bausteine-im-detail)
* [Teil 6 — Glossar](#teil-6--glossar)
* [Teil 7 — Was du nicht wissen musst](#teil-7--was-du-nicht-wissen-musst)

---

## Teil 1 — Was ein API-Layer eigentlich ist

Stell dir eine Bibliothek vor. Die Bücher stehen im Magazin (die Datenbank).
Es gibt zwei Möglichkeiten, wie Leser an Bücher kommen:

**Variante A — jeder geht selbst ins Magazin.** Schnell und ohne Umweg. Aber:
jeder braucht einen Schlüssel, jeder muss die Regalordnung kennen, und wenn
umsortiert wird, stehen alle im Dunkeln. Wenn jemand ein Buch falsch zurückstellt,
merkt es niemand.

**Variante B — es gibt einen Tresen.** Man sagt, was man braucht; jemand holt es.
Der Tresen kennt die Regalordnung, prüft den Ausweis, weiß welche Bücher gerade
gefragt sind und legt sie griffbereit.

Das ist der API-Layer. Die Dashboards gehen nicht mehr selbst ins Magazin,
sondern fragen am Tresen.

Heute ist es bei euch Variante A: `material_management_dashboard/data/repository.py`
enthält Cypher und die Neo4j-Zugangsdaten. Bei einem Dashboard ist das völlig in
Ordnung. Bei fünf Dashboards und mehreren Teams entstehen die typischen Probleme:
Zugangsdaten liegen fünffach herum, dieselbe Kennzahl wird fünfmal berechnet
(und ergibt irgendwann fünf verschiedene Zahlen), und eine Änderung am
Graphmodell bedeutet fünf Änderungen.

**Das ist der ganze Zweck.** Alles Weitere in diesem Dokument ist Detail — wie
der Tresen intern organisiert ist.

---

## Teil 2 — Die Geschichte in acht Schritten

### Schritt 1 (ab ~1993): Ein Programm pro Adresse

Am Anfang war eine Webadresse schlicht eine Datei auf der Festplatte.
`/cgi-bin/kunden.pl` war ein Programm, das der Server startete, wenn jemand
diese Adresse aufrief. Das Programm schrieb HTML auf die Standardausgabe, der
Server schickte es zurück.

```perl
# kunden.pl — das komplette "Framework" der frühen 90er
print "Content-Type: text/html\n\n";
print "<html><body>";
# ... Datenbank abfragen, HTML zusammenbauen ...
print "</body></html>";
```

**Was daran kaputt ging:** Für jeden einzelnen Aufruf startete das Betriebssystem
einen **neuen Prozess**. Das dauert (verglichen mit allem anderen) sehr lange.
Bei zehn Besuchern gleichzeitig ging der Server in die Knie. Und jede neue
Adresse bedeutete eine neue Datei — geteilter Code entstand per Copy-Paste.

> **Was davon übrig ist:** Die Erkenntnis, dass *Aufbauen und Wegwerfen teuer
> ist*. Sie taucht später bei Datenbankverbindungen exakt wieder auf.

---

### Schritt 2 (ab ~1995): Alles in einer Datei

PHP und ASP machten es bequemer: Code direkt in die HTML-Seite schreiben.

```php
<h1>Kunden</h1>
<?php
$verbindung = mysql_connect("db", "root", "geheim");
$ergebnis = mysql_query("SELECT * FROM kunden WHERE stadt = '" . $_GET["stadt"] . "'");
while ($zeile = mysql_fetch_array($ergebnis)) {
    echo "<tr><td>" . $zeile["name"] . "</td></tr>";
}
?>
```

Das war ein enormer Produktivitätssprung. Man sah sofort, was passiert.

**Was daran kaputt ging — vier Dinge auf einmal:**

1. **Alles vermischt.** Darstellung, Geschäftslogik und Datenbankzugriff standen
   in derselben Zeile. Man konnte die Berechnung nicht testen, ohne HTML zu
   erzeugen. Man konnte das Layout nicht ändern, ohne die Datenbankabfrage
   anzufassen.
2. **Sicherheitslücken.** In der Zeile oben klebt der Benutzereingabe-Wert
   `$_GET["stadt"]` direkt in der SQL-Abfrage. Gibt jemand
   `x'; DROP TABLE kunden; --` ein, ist die Tabelle weg. Das ist *SQL-Injection*,
   und es war jahrelang die verbreitetste Sicherheitslücke im Web.
3. **Verbindung pro Seitenaufruf.** `mysql_connect` bei jedem Aufruf neu — siehe
   Schritt 1, derselbe Fehler an anderer Stelle.
4. **Keine Wiederverwendung.** Zweite Seite mit denselben Kundendaten? Abfrage
   kopieren. Beim ersten Bugfix ändert man eine der Kopien und vergisst die anderen.

> **Was davon übrig ist:** Die Regel „Zugriff auf Daten wird von der Darstellung
> getrennt" — und zwar räumlich, in eigenen Dateien. Bei uns ist das
> `repositories/` gegenüber `products/`.

---

### Schritt 3 (ab ~2000): Schichten und Router

Die Antwort war, den Code nach **Verantwortung** zu zerlegen statt nach Seite.
Bekannt geworden als MVC (Model-View-Controller), populär durch Frameworks wie
Struts, später Rails und Django.

Zwei Ideen daraus sind für uns wichtig.

**Erstens: der Router.** Statt „Adresse = Datei" gibt es eine Tabelle, die
Adressen auf Funktionen abbildet:

```python
# Die Grundidee jedes Web-Frameworks seit ~2000
routes = {
    "/kunden":        zeige_kunden,
    "/kunden/neu":    lege_kunde_an,
}
```

Damit sind Adresse und Dateiname entkoppelt. Man kann eine Adresse umbenennen,
ohne Dateien zu verschieben.

**Zweitens: das Repository-Muster.** Der Datenbankzugriff wandert in eigene
Objekte. Der Rest des Programms ruft `kunden_repository.finde_alle()` auf und
weiß nicht, ob dahinter SQL, eine Datei oder ein fremder Dienst steckt.

**Was daran kaputt ging:** Die Frameworks wurden groß und meinungsstark. Vor
allem aber lösten sie ein Problem *nicht*, das mit dem Aufkommen von Handy-Apps
und JavaScript-Oberflächen dringend wurde: Bisher lieferte der Server **HTML**.
Eine App auf dem Telefon will kein HTML, sie will **Daten**.

---

### Schritt 4 (ab ~1999): Der Vertrag als eigene Datei — SOAP

Erster ernsthafter Versuch, Daten statt Seiten auszuliefern. Man beschrieb den
Dienst in einer maschinenlesbaren Datei (WSDL), und Werkzeuge erzeugten daraus
automatisch den Client-Code.

```xml
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <getKunde xmlns="http://beispiel.de/"><id>42</id></getKunde>
  </soap:Body>
</soap:Envelope>
```

Das war ein echter Fortschritt: Es gab wieder einen **Vertrag**, und der war
maschinenlesbar. Genau deshalb kommt die Idee später zurück.

**Was daran kaputt ging:** Der Aufwand. Für „gib mir Kunde 42" schrieb man
dreißig Zeilen XML. Man brauchte schwere Werkzeuge, um überhaupt eine Anfrage zu
stellen; im Browser ging es praktisch gar nicht. Die Verträge waren so starr,
dass jede kleine Änderung eine Neugenerierung auf allen Seiten auslöste.

> **Der Kompromiss, um den es hier geht — er zieht sich durch alles Weitere:**
> Ein Vertrag macht Zusammenarbeit sicher, aber ein zu strenger Vertrag macht
> Änderungen teuer.

---

### Schritt 5 (ab ~2005): REST und JSON — leicht, aber vertragslos

Die Gegenbewegung. Statt einer eigenen Nachrichtensprache benutzt man einfach
das, was HTTP schon kann: Adressen für Dinge, und die HTTP-Methode sagt, was
passieren soll.

```
GET    /kunden/42      hol mir Kunde 42
POST   /kunden         lege einen neuen an
PATCH  /kunden/42      ändere Teile von Kunde 42
DELETE /kunden/42      lösche ihn
```

Und als Datenformat JSON statt XML:

```json
{"id": 42, "name": "Meier", "stadt": "Köln"}
```

Das war radikal einfacher. Man konnte eine API im Browser ausprobieren. JSON
ließ sich in JavaScript direkt verwenden. REST wurde binnen weniger Jahre der
Standard — zu Recht.

**Was daran kaputt ging:** Der Vertrag war weg. Es stand nirgends, welche Felder
`/kunden/42` liefert. Also schrieb man Dokumentation — in einem Wiki, einem PDF,
einer Word-Datei.

Und diese Dokumentation war **nach drei Wochen falsch**. Nicht aus Nachlässigkeit,
sondern strukturell: Wer ein Feld umbenennt, ändert den Code. Die Doku liegt
woanders. Nichts erzwingt, dass beide zusammenpassen.

Das führte zu einem Alltag, den fast jeder kennt, der um 2010 herum APIs benutzt
hat: Man liest die Doku, baut den Client, es funktioniert nicht, man probiert
herum, bis man herausfindet, wie es *wirklich* heißt.

Dazu kam das zweite Problem: Ohne Vertrag prüfte niemand die Eingaben. Schickte
jemand `{"alter": "dreiundvierzig"}`, flog irgendwo tief im Code eine Exception —
und die API antwortete mit einem HTML-Stacktrace statt einer verständlichen
Fehlermeldung.

> **Beides sind exakt die Probleme, die FastAPI heute löst.** Ohne diesen Schritt
> wirkt FastAPI wie unnötige Zeremonie. Mit ihm wird klar, wogegen es hilft.

---

### Schritt 6 (ab ~2011): Doku aus dem Code — Swagger/OpenAPI

Die Einsicht: Dokumentation, die von Hand gepflegt wird, driftet immer. Also muss
sie **aus dem Code entstehen**.

Swagger (später in OpenAPI umbenannt) definierte ein Format, in dem eine API sich
selbst beschreibt: Adressen, Parameter, Felder, Typen — als maschinenlesbare
Datei. Daraus lassen sich erzeugen: eine interaktive Doku-Seite zum Ausprobieren,
Client-Code für diverse Sprachen, und automatische Tests.

Das ist im Kern **die gute Idee von SOAP (Schritt 4), aber auf dem leichten
Fundament von REST (Schritt 5)**. Deshalb hat es sich durchgesetzt.

**Was daran kaputt ging:** Anfangs schrieb man die OpenAPI-Datei *von Hand* —
oder pflegte umfangreiche Kommentare über jeder Funktion:

```python
@app.route("/kunden/<int:id>")
def hole_kunde(id):
    """
    ---
    parameters:
      - name: id
        in: path
        type: integer
        required: true
    responses:
      200:
        schema:
          properties:
            name: {type: string}
    """
    ...
```

Damit hat man die Doku zwar *näher* an den Code gerückt, aber es sind immer noch
**zwei Wahrheiten**. Wer den Rückgabewert ändert und den Kommentar vergisst, hat
wieder eine falsche Doku — nur diesmal in derselben Datei.

---

### Schritt 7 (ab ~2015): Eine Wahrheit — Type Hints, Pydantic, FastAPI

Parallel passierte in Python etwas anderes: **Typannotationen** (PEP 484, 2015).

```python
def addiere(a: int, b: int) -> int:
    return a + b
```

Python selbst ignoriert diese Angaben zur Laufzeit. Sie waren zunächst nur für
Editoren und Prüfwerkzeuge gedacht.

Dann kam jemand auf die entscheidende Idee: **Man kann sie auslesen und
tatsächlich benutzen.** Die Bibliothek Pydantic macht genau das:

```python
from pydantic import BaseModel

class Kunde(BaseModel):
    id: int
    name: str
    stadt: str | None = None
```

Aus dieser einen Klasse folgt:

* eine **Validierung** — `Kunde(id="42")` wandelt in `42`; `Kunde(id="abc")`
  wirft einen klaren Fehler mit Feldnamen,
* eine **Beschreibung** in OpenAPI-Form,
* **Editor-Unterstützung** — Autovervollständigung und Tippfehler-Warnung.

FastAPI (2018) verband das mit dem Web-Teil. Man schreibt nur noch:

```python
@app.get("/kunden/{kunde_id}")
async def hole_kunde(kunde_id: int) -> Kunde:
    ...
```

und bekommt daraus automatisch: Routing, Umwandlung von `kunde_id` aus dem Text
der URL in eine echte Zahl, Fehler 422 bei Unsinn, die OpenAPI-Beschreibung und
eine interaktive Doku unter `/docs`.

**Das ist die Antwort auf deine Frage „warum Annotations".** Es geht nicht um
Ordnung oder Stil. Die Annotation ist die **eine Quelle**, aus der Validierung
*und* Dokumentation *und* Editor-Hilfe entstehen. Vorher waren das drei
Wahrheiten, die auseinanderdrifteten. Jetzt ist es eine, die nicht driften kann.

Vergleich am selben Beispiel:

| | Universitätsstil (Schritt 5) | Heute (Schritt 7) |
|---|---|---|
| Parameter lesen | `request.args.get("limit")` → Text | `limit: int = 100` → geprüfte Zahl |
| Ungültige Eingabe | irgendwo tief im Code eine Exception | 422 mit Feldname und Grund |
| Dokumentation | separates Wiki, driftet | `/docs`, kann nicht driften |
| Editor kennt die Felder | nein | ja |
| Zeilen Code | mehr | weniger |

Der letzte Punkt überrascht oft: Der moderne Weg ist nicht nur sicherer, er ist
auch **kürzer**.

---

### Schritt 8 (ab ~2019): Datenprodukte

Der jüngste Schritt und der, der eure konkrete Situation betrifft.

Als Unternehmen anfingen, ihre Daten breit intern verfügbar zu machen, wiederholte
sich ein Muster: Ein zentrales Datenteam bekam Anfragen von allen Seiten
(„wir bräuchten mal die Materialdaten mit Lieferantenbezug") und wurde zum
Flaschenhals. Gleichzeitig fragte niemand mehr nach, was es schon gab — also
wurde dieselbe Auswertung mehrfach gebaut, mit leicht verschiedenen Ergebnissen.

Die Antwort (unter dem Namen *Data Mesh* bekannt geworden) war ein
Perspektivwechsel: Ein Datensatz ist kein Nebenprodukt, sondern ein **Produkt**
mit denselben Eigenschaften wie eine Software:

* Es hat einen **Namen**, unter dem man es findet.
* Es hat einen **Besitzer**, den man fragen kann.
* Es hat einen **Vertrag** — feste Felder, feste Bedeutung.
* Es hat **Versionen** und einen Lebenszyklus.
* Es ist **auffindbar** — es gibt einen Katalog.

Genau das ist unser `@data_product`-Dekorator. Wenn du dich fragst, warum wir
nicht einfach Routen schreiben, sondern eine Registry haben: **weil ein Name,
ein Owner und ein Katalog sonst nirgends stehen**. Eine Route hat keinen Besitzer.
Ein Datenprodukt hat einen.

---

## Teil 3 — Vier Nebenstränge

Diese vier Themen haben eine eigene Geschichte, die parallel lief.

### 3.1 Verbindungen: warum es „Treiber" und „Session" gibt

**Der naive Weg (Schritt 2 oben):** Bei jedem Aufruf zur Datenbank verbinden,
Abfrage schicken, Verbindung schließen.

**Was daran kaputt ging:** Eine Datenbankverbindung aufzubauen ist überraschend
teuer — Netzwerkverbindung herstellen, verschlüsselte Verbindung aushandeln,
anmelden. Das dauert leicht 50–200 Millisekunden. Wenn die eigentliche Abfrage
2 ms braucht, verbringt man 98 % der Zeit mit Vorbereitung.

**Das Gegenmittel: Verbindungs-Pooling.** Man baut beim Programmstart einige
Verbindungen auf und hält sie offen. Wer eine braucht, leiht sich eine aus und
gibt sie zurück.

Genau das ist der **Treiber** bei Neo4j bzw. die **Engine** bei SQLAlchemy: ein
Objekt, das den Vorrat an Verbindungen verwaltet.

```
TREIBER / ENGINE   = der Verleih.    Einer pro Programm, lebt die ganze Laufzeit.
SESSION            = die Ausleihe.   Eine pro Anfrage, danach zurückgegeben.
```

**Und was daran wieder kaputt ging:** Manche machten daraus „dann nehme ich eben
auch nur *eine* Session für alles". Das geht schief, weil eine Session einen
Zustand hat (welche Abfrage läuft gerade, welche Transaktion ist offen). Wenn
zwei Anfragen gleichzeitig dieselbe Session benutzen, vermischen sich ihre
Ergebnisse. Der Effekt: sporadische, nicht reproduzierbare Fehler unter Last —
die unangenehmste Fehlerklasse überhaupt.

Daher die Regel, die in unserem Code an mehreren Stellen als Kommentar steht:

> Treiber langlebig und geteilt. Session kurzlebig und exklusiv.

In unserem Code: Treiber in `application.py` (Lifespan), Session in
`api/deps.py` (Dependency). Und weil das Zurückgeben leicht vergessen wird,
übernimmt es ein `AsyncExitStack` automatisch — auch wenn zwischendurch ein
Fehler auftritt.

### 3.2 Gleichzeitigkeit: warum `async`

**Stufe 1 (Schritt 1 oben): ein Prozess pro Anfrage.** Sicher getrennt, aber
extrem teuer. Bei hundert gleichzeitigen Nutzern hundert Prozesse.

**Stufe 2: ein Thread pro Anfrage.** Deutlich billiger als ein Prozess, aber
nicht umsonst — jeder Thread belegt Speicher, und das Umschalten zwischen ihnen
kostet Zeit. Etwa ab tausend gleichzeitigen Verbindungen wurde das zum Problem.
Das bekam sogar einen Namen: **das C10K-Problem** (1999) — „wie bedient man
zehntausend Verbindungen gleichzeitig?"

**Die Beobachtung dahinter:** Ein Webserver *rechnet* fast nie. Er **wartet** —
auf die Datenbank, auf einen anderen Dienst, auf das Netzwerk. Ein Thread, der
wartet, blockiert Speicher, ohne etwas zu tun.

**Stufe 3: ein einzelner Ablauf, der beim Warten weiterspringt.** Statt hundert
Threads gibt es einen Kreislauf (den *Event Loop*), der hundert Anfragen
verwaltet. Sobald eine auf die Datenbank wartet, arbeitet er an einer anderen
weiter. In Python schreibt man das mit `async def` und `await`:

```python
async def hole_daten():
    rows = await datenbank.abfrage(...)   # "await" = hier darf gewechselt werden
    return rows
```

`await` markiert die Stellen, an denen gewartet wird — und damit die Stellen, an
denen etwas anderes drankommen darf.

**Der Haken, der bis heute Leute kostet:** Das funktioniert nur, wenn *alle*
Wartestellen so markiert sind. Ruft man mitten in einer `async`-Funktion etwas
auf, das blockiert (den synchronen Neo4j-Treiber, `requests`, `time.sleep`), dann
hält der ganze Kreislauf an — **alle** parallelen Anfragen stehen still, nicht
nur die eigene. Das ist der häufigste schwere Fehler in async-Python.

Deshalb:

* Wir benutzen durchgehend die **asynchronen** Treiber
  (`AsyncGraphDatabase`, `postgresql+asyncpg://`).
* Wer beim synchronen Treiber bleiben will, schreibt den Endpunkt als `def`
  statt `async def` — dann schiebt FastAPI ihn selbst in einen Thread. Das ist
  eine völlig legitime Wahl mit weniger Fallstricken; sie skaliert nur etwas
  schlechter.
* **Was man nie tun darf, ist mischen.**

### 3.3 Versionierung: warum `/v2`

**Der naive Weg:** Es gibt eine API, sie wird bei Bedarf geändert.

**Was daran kaputt ging:** Sobald jemand anderes deine API benutzt, ist jede
Änderung ein Risiko. Du benennst `bezeichnung` in `description` um — und irgendwo
zeigt ein Dashboard leere Spalten. Du merkst es nicht, weil es dein Dashboard
nicht ist.

**Erstes Gegenmittel: „wir sagen vorher Bescheid."** Funktioniert bei zwei
Teams. Bei zehn nicht mehr — irgendwer ist im Urlaub, irgendwer liest die Mail
nicht.

**Zweites Gegenmittel: Versionen.** Die alte Fassung bleibt erreichbar, die neue
kommt daneben. Jeder migriert, wenn er kann.

**Was daran wieder kaputt ging:** Wenn *jede* Änderung eine neue Version bedeutet,
hat man nach einem Jahr `/v17` und muss siebzehn Fassungen pflegen. Das ist so
mühsam, dass Teams anfangen, Änderungen heimlich in die alte Version zu schieben
— womit man wieder am Anfang ist.

**Die heutige Lösung ist eine Unterscheidung:**

* Ein **hinzugefügtes** Feld bricht nichts. Wer es nicht kennt, ignoriert es.
  → keine neue Version nötig (nur die Unternummer steigt, `2.0` → `2.1`).
* Ein **entferntes oder umbenanntes** Feld bricht. → neue Version, `/v3`.

Deshalb steht bei uns im Pfad nur die Hauptnummer (`/v2`) und die volle Nummer
(`2.1`) in der Antwort. Ein Dashboard, das gegen `/v2` gebaut ist, kann durch
kleine Verbesserungen nicht kaputtgehen.

**Der Sonderfall, den man leicht übersieht:** Wenn sich die *Berechnung* hinter
einem Feld ändert — etwa die Formel für `risiko_score` — bleibt das Schema
identisch, aber die Zahlen bedeuten etwas anderes. Das ist eine brechende
Änderung, obwohl kein Typ sich rührt. Solche Fälle sind gefährlicher als
umbenannte Felder, weil nichts kaputtgeht; es wird nur still falsch.

### 3.4 Caching: warum ETag

**Der naive Weg:** Jede Anfrage geht bis zur Datenbank durch.

**Was daran kaputt ging:** Ein Dashboard fragt dieselben Daten dauernd an — bei
jedem Klick, jedem Filterwechsel, jedem Nutzer, jedem Neuladen. Eine Abfrage, die
800 ms braucht, läuft dann vierzigmal pro Minute.

**Erstes Gegenmittel: jeder Client merkt sich die Antwort selbst.** Führte zu der
Frage, die als Running Gag der Informatik gilt: *Woher weiß der Client, wann seine
Kopie veraltet ist?*

**Zweites Gegenmittel, seit HTTP/1.1 (1997) eingebaut: der Server gibt der
Antwort einen Fingerabdruck mit** — das **ETag**.

```
Erste Anfrage:
  Client:  GET /material-overview/v2
  Server:  200 OK, ETag: "a3f9..."          + 500 KB Daten

Zweite Anfrage, kurz darauf:
  Client:  GET /material-overview/v2, If-None-Match: "a3f9..."
  Server:  304 Not Modified                 (kein Body!)
```

Der Client fragt sinngemäß: „Ich habe Stand a3f9 — hat sich was geändert?"
Wenn nicht, kommt eine leere Antwort zurück. Das spart Bandbreite und erspart dem
Dashboard das Neuzeichnen einer großen Tabelle.

Zusätzlich hält unser Server die berechneten Ergebnisse einige Sekunden im
Arbeitsspeicher (`cache_ttl`). Der Schlüssel dafür ist
`(Produkt, Version, Parameter)` — **die Parameter gehören zwingend dazu**.
Sie zu vergessen ist der klassische Cache-Fehler: Dann bekommt der Nutzer mit
Filter „Werk Köln" die Antwort desjenigen, der vorher „Werk Berlin" gefiltert hat.

### 3.5 Fehlerformate: warum ein einheitliches Format

**Der naive Weg:** Jede API meldet Fehler, wie es gerade passt — mal
`{"error": "..."}`, mal `{"message": "..."}`, mal HTML.

**Was daran kaputt ging:** Jeder Client braucht für jede API eine eigene
Fehlerbehandlung. Bei fünf Dashboards, die dieselbe API benutzen, steht dieselbe
Logik fünfmal da.

**Das Gegenmittel:** ein standardisiertes Format (RFC 9457, „Problem Details").
Immer dieselben Felder: `title`, `status`, `detail`, plus was man selbst ergänzt.

Wichtig sind vor allem zwei Statuscodes, weil sie für den Client
unterschiedliches *Verhalten* bedeuten:

* **503** — die Datenquelle ist gerade nicht erreichbar. Heißt: *später nochmal*.
* **500** — ein Fehler in unserem Code. Heißt: *bitte melden*.

Deshalb hat unser Code eigene Fehlerklassen (`UpstreamUnavailableError` vs.
`AppError`) statt überall `raise Exception`.

---

## Teil 4 — Ein Request durch unseren Code

Damit die Teile zusammenkommen, hier ein konkreter Aufruf, Station für Station.
Das Dashboard fragt:

```
GET /api/v1/data-products/material-overview/v2?status=Gesperrt&limit=50
```

**1. Middleware** (`core/middleware.py`)
Vergibt eine Request-ID (z. B. `1fca65e7ef6b`), startet die Zeitmessung. Die ID
landet in jeder Logzeile und im Antwort-Header. → *Damit du später „bei mir war
die Tabelle leer" mit einer konkreten Logzeile verbinden kannst.*

**2. Routing** (FastAPI)
Findet die passende Funktion. Diese Route wurde **beim Programmstart erzeugt**,
nicht von Hand geschrieben — die Registry hat sie aus dem Datenprodukt gebaut.

**3. Parameter prüfen** (`products/base.py::ProductParams`)
`status=Gesperrt` wird zur Liste `["Gesperrt"]`, `limit=50` zur Zahl `50`. Ein
unbekannter Parameter (`?stauts=...`) führt hier zu 422 — *nicht* dazu, dass
still ungefiltert geliefert wird. → *Der Tippfehler fällt beim Entwickeln auf,
nicht im Management-Meeting.*

**4. Authentifizierung** (`core/security.py`)
Ermittelt, wer fragt. Aktuell per API-Key, später per Login-Token. Ergebnis ist
ein `Principal` mit Gruppen — der Haken, an dem später Berechtigungen hängen.

**5. Cache prüfen** (`products/cache.py`)
Schlüssel aus Produkt, Version und Parametern. Treffer → weiter bei Schritt 9.

**6. Datenzugriff vorbereiten** (`api/deps.py`, `db/repositories.py`)
Der `Repositories`-Container wird gebaut. Er öffnet noch **nichts** — erst wenn
das Produkt tatsächlich fragt. Alles, was er öffnet, wird am Ende automatisch
geschlossen.

**7. Das Datenprodukt läuft** (`products/catalog/material_overview_v2.py`)

```python
async def load(repos, params):
    repo = await repos.materials()          # hier öffnet sich die Neo4j-Session
    return transform(await repo.fetch_materials(), params)
```

`fetch_materials()` schickt den Cypher. `transform()` filtert und rechnet den
Bestandswert aus — **ohne Datenbank, ohne HTTP**, und ist deshalb in
Millisekunden testbar.

**8. Antwort zusammenbauen** (`products/router.py`)
Umschlag mit Metadaten (Produkt, Version, Zeitstempel, Zeilenzahl, Quelle),
Paginierung, ETag, Cache-Control. Bei veralteten Versionen zusätzlich
`Deprecation`- und `Sunset`-Header.

**9. Session schließen**
Der `AsyncExitStack` schließt die Neo4j-Session — auch dann, wenn zwischendurch
ein Fehler aufgetreten ist.

**10. Antwort geht raus**

```json
{
  "meta": {"product": "material-overview", "version": "2.0",
           "generated_at": "2026-08-20T07:09:05Z", "row_count": 8,
           "total_count": 8, "source": "neo4j", "cache": "miss"},
  "data": [{"material_nr": "MAT-100777", "bestandswert": 4947.5, ...}]
}
```

Und falls unterwegs etwas schiefgeht, greift `core/errors.py` und macht daraus
ein einheitliches Fehlerformat mit derselben Request-ID.

---

## Teil 5 — Vier Bausteine im Detail

Vier Stellen, die beim Lesen des Codes am häufigsten Fragen auslösen.

---

### 5.1 Datenzugriff: warum drei Orte statt einem

Die berechtigte Frage aus dem Review: *„Was genau soll ein Repository sein?
Die meisten Datenprodukte werden vermutlich nur eine Cypher Query sein."*

Sehen wir uns zuerst an, was die drei Orte tatsächlich tun.

#### Die drei Orte

**`db/neo4j.py` und `db/sql.py` — die Verbindung aufbauen**

Nur Infrastruktur, keine Fachlichkeit. Wie baue ich einen Neo4j-Treiber, wie
mache ich ihn wieder zu, wie sieht ein Postgres-DSN aus. Zusammen etwa 60 Zeilen,
und sie werden praktisch nie wieder angefasst.

```python
async def create_driver(uri, auth) -> AsyncDriver | None:   # beim Programmstart
async def close_driver(driver) -> None:                     # beim Beenden
```

**`repositories/materials.py` — die Abfragen**

Hier steht der Cypher. Und die Methoden, die ihn ausführen.

```python
_MATERIALS_CYPHER = """MATCH (m:Material) ... RETURN ..."""

class Neo4jMaterialsRepository:
    def __init__(self, session): self._session = session
    async def fetch_materials(self): ...
    async def fetch_suppliers(self): ...
```

**`db/repositories.py` — der Verteiler**

Ein Objekt, das pro Anfrage lebt und den Datenprodukten die passenden Abfrage-
Objekte reicht. Es öffnet Sessions erst, wenn sie gebraucht werden, teilt sie
zwischen mehreren Abfragen und schließt sie am Ende zuverlässig.

```python
async def materials(self) -> MaterialsRepository:
    session = await self._neo4j_session()      # öffnet lazy, schließt automatisch
    return Neo4jMaterialsRepository(session)
```

#### Was ein „Repository" ist — ohne Fachjargon

> **Ein Repository ist die eine Stelle, an der Abfragen zu einem Thema stehen.**

Mehr nicht. Der Name kommt aus einem Buch von 2003 und ist unglücklich abstrakt;
„die Datei mit den Material-Abfragen" beschreibt es genauso gut.

#### Der Einwand ist teilweise berechtigt

Wenn ein Datenprodukt nur eine Cypher-Abfrage ist — warum steht die Abfrage nicht
einfach im Datenprodukt? Also so:

```python
# Die einfache Variante — alles in einer Datei
@data_product(name="material-overview", version="2.0", ...)
async def load(session, params):
    result = await session.run("MATCH (m:Material) RETURN ...")
    return transform(await result.data(), params)
```

Das ist verlockend, und für **ein** Datenprodukt wäre es besser. Der Grund, warum
es hier trotzdem getrennt ist, lässt sich an unserem eigenen Code ablesen:

```
material-overview v1  ─┐
material-overview v2  ─┼──► fetch_materials()  ──►  EIN Cypher
supplier-risk    v1  ─┘
```

Drei Datenprodukte, eine Abfrage. Stünde der Cypher in den Produkten, gäbe es ihn
dreimal — und eine Änderung am Graphmodell hieße, drei Dateien synchron zu halten
und zu hoffen, dass niemand eine vergisst. Das ist derselbe Grund, aus dem im
Dashboard `data/schema.py` existiert: Spalten waren dort früher an drei Stellen
definiert, und genau das war das Problem.

Dazu kommt der Session-Lebenszyklus. Irgendwer muss die Session öffnen, zwischen
mehreren Abfragen teilen und danach zuverlässig schließen — auch wenn ein Fehler
auftritt. Steht das in jedem Datenprodukt, muss jeder Autor es richtig machen.

#### Was am Einwand stimmt: zu viel Zeremonie

Zwei Dinge sind tatsächlich komplizierter als nötig.

**Erstens die Benennung.** `db/repositories.py` und `repositories/` sind zwei
verschiedene Dinge mit fast demselben Namen. Das ist schlicht ein Fehler beim
Entwurf gewesen.

**Zweitens die `Protocol`-Klassen.** In `repositories/materials.py` steht:

```python
@runtime_checkable
class MaterialsRepository(Protocol):        # <- braucht es das?
    async def fetch_materials(self) -> list[dict]: ...

class Neo4jMaterialsRepository:             # die einzige Umsetzung
    ...
```

Ein `Protocol` beschreibt „welche Methoden muss so ein Objekt haben" und ist
sinnvoll, wenn es **mehrere** Umsetzungen gibt. Ursprünglich gab es die: eine für
Neo4j und eine mit Beispieldaten. Die Beispieldaten sind inzwischen raus — damit
hat das Protocol genau eine produktive Umsetzung und rechtfertigt sich nicht mehr
von selbst.

#### Konkreter Vorschlag

Drei Änderungen, die den Einwand aufnehmen, ohne die echte Wiederverwendung zu
verlieren:

| Vorher | Nachher | Warum |
|---|---|---|
| `repositories/materials.py` | `queries/materials.py` | Sagt was drin ist. Kein Fachbegriff nötig. |
| `db/repositories.py` mit `class Repositories` | `db/sources.py` mit `class Sources` | Beendet die Namenskollision |
| `MaterialsRepository(Protocol)` + `Neo4jMaterialsRepository` | nur `MaterialQueries` | Eine Klasse statt zwei; das Protocol war für den entfallenen zweiten Adapter da |

Danach bleiben drei Begriffe statt fünf, und ein Datenprodukt liest sich so:

```python
async def load(sources, params):
    queries = await sources.materials()
    return transform(await queries.fetch_materials(), params)
```

Die Ersparnis ist überschaubar (rund 40 Zeilen), der Gewinn an Erklärbarkeit
groß. Und: Wenn später doch eine zweite Datenquelle für dieselben Daten dazukommt,
lässt sich das Protocol in zehn Minuten nachrüsten — man hat also nichts verbaut.

#### Was **nicht** wegfallen sollte

Der Verteiler (`Sources`) bleibt, auch wenn er nach Zeremonie aussieht. Er ist
das, was die Datenprodukte von Sessions, Treibern und Aufräumen freihält — und
er ist der Grund, warum in keinem Datenprodukt jemals `session.close()` steht.
Der nächste Abschnitt zeigt, warum das technisch so sein muss.

> **Vorschlag für die Antwort im Review:** dem Einwand bei Protocol und Benennung
> zustimmen und beides umbauen; die Trennung Abfragen/Produkte behalten mit dem
> konkreten Beispiel, dass drei Produkte heute schon eine Abfrage teilen.

---

### 5.2 Die Registry — wie aus einer Datei eine Route wird

Die Registry (`products/registry.py`) ist ein Verzeichnis aller Datenprodukte.
Sie ist ungefähr 100 Zeilen und besteht aus drei Teilen.

#### Teil 1: ein Verzeichnis

Im Kern ein Wörterbuch. Schlüssel ist `(Name, Hauptversion)`, Wert das
Datenprodukt:

```python
{("material-overview", 1): DataProduct(...),
 ("material-overview", 2): DataProduct(...),
 ("supplier-risk",     1): DataProduct(...)}
```

Beim Eintragen wird geprüft, ob der Schlüssel schon belegt ist. Wären zwei
Produkte unter `material-overview` + `v2` registriert, gäbe es zwei Routen mit
demselben Pfad — und welche antwortet, hinge von der Importreihenfolge ab. Ein
Startfehler ist besser als ein Zufall.

#### Teil 2: der Dekorator

Ein Dekorator ist eine Funktion, die eine andere Funktion entgegennimmt. Diese
beiden Schreibweisen sind identisch:

```python
@data_product(name="material-overview", version="2.0", ...)
async def load(sources, params): ...

# ist exakt dasselbe wie:
async def load(sources, params): ...
load = data_product(name="material-overview", version="2.0", ...)(load)
```

`data_product(...)` gibt eine Funktion zurück, die `load` bekommt, daraus ein
`DataProduct`-Objekt baut, es in die Registry legt und `load` unverändert
zurückgibt. Der entscheidende Punkt:

> **Der Dekorator läuft, sobald die Datei importiert wird.** Nicht wenn jemand
> `load()` aufruft. Ein Import genügt, damit sich das Produkt anmeldet.

#### Teil 3: die Datei finden

Damit der Dekorator läuft, muss die Datei importiert werden. Statt einer Liste,
die jemand pflegen müsste, sieht `discover()` einfach nach, welche Dateien im
Katalogordner liegen:

```python
def discover(package="data_api.products.catalog"):
    module = importlib.import_module(package)
    for info in pkgutil.iter_modules(module.__path__):   # liest das Verzeichnis
        importlib.import_module(f"{package}.{info.name}")
```

Deshalb reicht es, eine Datei abzulegen: Sie wird gefunden, importiert, der
Dekorator läuft, das Produkt steht im Verzeichnis.

#### Der Ablauf beim Start

```
1. create_app()
2.   discover()                    ── importiert products/catalog/*.py
3.                                    └─ jeder @data_product meldet sich an
4.   build_products_router()       ── läuft die Registry durch und baut
5.                                    pro Eintrag eine echte FastAPI-Route
6.   app.include_router(...)
```

Schritt 4 ist der interessante. Für jedes Produkt entsteht eine Funktion,
die dieses eine Produkt bedient:

```python
for product in registry.all():
    endpoint = _make_endpoint(product)        # Funktion für genau dieses Produkt
    router.add_api_route(
        f"/{product.name}/{product.path_version}",
        endpoint,
        response_model=ProductEnvelope[product.item_model],   # <- eigenes Schema!
        ...
    )
```

`response_model=` ist der Grund für den ganzen Aufwand. Damit weiß FastAPI, welche
Felder diese eine Route liefert — und kann daraus die Dokumentation unter `/docs`
erzeugen. Bei einer einzigen generischen Route (`/{name}/{version}`) stünde dort
nur „gibt irgendein JSON zurück".

#### Wo es aufhört, magisch zu sein

Die Reihenfolge ist die einzige echte Bedingung: `discover()` **vor**
`build_products_router()`. Wäre es umgekehrt, wäre das Verzeichnis beim
Routenbauen noch leer und die API hätte null Datenprodukte. Beides steht direkt
untereinander in `application.py::create_app()`.

Und eine Stolperfalle, die uns beim Bauen erwischt hat: In `products/router.py`
darf **kein** `from __future__ import annotations` stehen. Die Typangaben der
erzeugten Funktionen sind zur Laufzeit erzeugte Objekte; mit dieser Zeile würden
sie zu Text, und FastAPI könnte sie nicht mehr auflösen. Es steht als Kommentar in
der Datei — bitte nicht „aufräumen".

---

### 5.3 Dependency Injection — was, warum, wie

Der Begriff klingt größer als die Sache.

> **Dependency Injection heißt: eine Funktion holt sich ihre Hilfsmittel nicht
> selbst, sondern bekommt sie gereicht.**

#### Die drei Möglichkeiten

**Variante A — selbst besorgen.**

```python
async def hole_materialien():
    driver = AsyncGraphDatabase.driver(os.getenv("NEO4J_URI"), auth=...)
    async with driver.session() as session:
        return await session.run("MATCH (m:Material) ...")
```

Probleme: Bei jedem Aufruf ein neuer Treiber (der Verbindungspool wird
weggeworfen). Nicht testbar ohne echte Datenbank. Die Konfiguration wird mitten
in der Fachlogik gelesen.

**Variante B — globales Objekt.**

```python
driver = AsyncGraphDatabase.driver(...)      # irgendwo auf Modulebene

async def hole_materialien():
    async with driver.session() as session: ...
```

Besser bei der Performance, aber: Die Verbindung entsteht beim **Import**. Jedes
`import`, auch das von pytest beim Einsammeln der Tests, verbindet sich zur
Datenbank. Für Tests müsste man das Modul-Global von außen ersetzen
(*monkeypatching*) — und dann teilen sich alle Tests dasselbe Objekt.

**Variante C — gereicht bekommen (Dependency Injection).**

```python
async def hole_materialien(session = Depends(get_session)):
    return await session.run("MATCH (m:Material) ...")
```

Die Funktion sagt nur noch *was* sie braucht. Wer es liefert und wann es
aufgeräumt wird, steht woanders.

#### Wie FastAPI das technisch macht

Beim Start liest FastAPI mit `inspect.signature()` die Signatur jeder
Endpunktfunktion. Für jeden Parameter entscheidet es anhand der Typangabe:

| Parameter | FastAPI erkennt | Woher der Wert kommt |
|---|---|---|
| `kunde_id: int` und `{kunde_id}` steht im Pfad | Pfadparameter | aus der URL, in `int` umgewandelt |
| `limit: int = 100` | Query-Parameter | aus `?limit=...` |
| `daten: KundeIn` (Pydantic-Modell) | Request-Body | aus dem JSON |
| `session = Depends(get_session)` | **Dependency** | Ergebnis von `get_session()` |

Aus diesen Angaben entsteht einmalig ein Abhängigkeitsbaum. Für unsere
Datenprodukt-Route sieht er so aus:

```
endpoint
├── params      Annotated[MaterialParamsV2, Query()]
├── repos       Depends(get_repositories)
│               └── settings   Depends(get_settings)
└── principal   Depends(current_principal)
                └── settings   Depends(get_settings)   ← nur EINMAL ausgewertet
```

Drei Eigenschaften, die den Unterschied machen:

**Zwischenspeichern pro Anfrage.** `get_settings` steht zweimal im Baum, wird
aber nur einmal aufgerufen. Bei einer Datenbank-Session ist das entscheidend:
Zwei Abfragen bekommen dieselbe Session, nicht zwei.

**Aufräumen mit `yield`.** Eine Dependency, die `yield` benutzt, ist zweigeteilt:

```python
async def get_repositories(request, settings):
    async with AsyncExitStack() as stack:
        yield Repositories(stack=stack, ...)     # alles davor: vor dem Endpunkt
    # alles danach: NACH der Antwort — auch wenn der Endpunkt einen Fehler warf
```

Deshalb steht in keinem Datenprodukt jemals `session.close()`.

**Austauschbar in Tests.** Das ist der praktische Hauptgewinn:

```python
app.dependency_overrides[get_repositories] = FakeRepositories
```

Eine Zeile, und die gesamte Datenschicht ist ersetzt. Kein Monkeypatching, keine
Umgebungsvariablen, keine laufende Datenbank. Route, Validierung, Produkt-Logik,
Umschlag, Cache und Header laufen dabei **echt** — nur die unterste Schicht ist
getauscht. Genau darauf beruhen die 47 Tests der API.

Das Dashboard nutzt dasselbe Prinzip an anderer Stelle: `httpx.MockTransport`
ersetzt nur die HTTP-Schicht, alles darüber läuft echt
(`tests/test_repository.py`).

#### Und was, wenn man es nicht braucht?

Für ein Skript: nicht nötig. Der Nutzen entsteht, sobald es (a) etwas gibt, das
länger lebt als eine Anfrage (Verbindungspools), (b) etwas zuverlässig aufgeräumt
werden muss, und (c) Tests ohne die echte Infrastruktur laufen sollen. Alle drei
treffen auf diesen Dienst zu.

---

### 5.4 Wie ein Dashboard die API benutzt

`clients/dash_client.py` im API-Projekt ist eine **Vorlage zum Kopieren**, kein
Modul, das ein Dashboard importiert.

Der Grund ist praktisch: `api/` ist ein eigenes Projekt mit eigener virtueller
Umgebung und hängt an FastAPI, dem Neo4j-Treiber und SQLAlchemy. Nichts davon
soll ins Dashboard, das nur `httpx` braucht. Also liegt im Dashboard eine Kopie
unter `data/api_client.py`.

> Sobald das **dritte** Dashboard denselben Client benutzt, lohnt sich ein kleines
> gemeinsames Paket. Bei zweien ist Kopieren billiger als die Paketverwaltung —
> und der Client ist generisch (er kennt kein einziges Feld eines Datenprodukts),
> die Kopien driften also nicht fachlich auseinander.

#### Was sich beim Umbau geändert hat

```
vorher:   Dashboard ──Bolt/Cypher──► Neo4j
nachher:  Dashboard ──HTTP/JSON────► API-Layer ──► Neo4j
```

| Datei | Vorher | Nachher |
|---|---|---|
| `data/neo4j.py` | Treiber, Zugangsdaten | **gelöscht** |
| `data/repository.py` | Cypher + Mock-Daten | ruft den API-Client |
| `data/api_client.py` | — | **neu**, kennt HTTP |
| `app.py` | legt Treiber an | nichts mehr davon |
| `requirements.txt` | `neo4j` | `httpx` |

**Alles andere blieb unangetastet** — Filter, KPIs, Tabelle, Callbacks, Sidebar.
Der Grund: Sie kannten schon vorher nur `get_materials()`. Genau dafür war die
Datenschicht von Anfang an auf eine Datei isoliert.

#### Die zwei interessanten Stellen

**Ein Client pro Prozess, nicht pro Callback:**

```python
_client = DataProductClient()      # hält den Verbindungspool offen
```

Derselbe Gedanke wie beim Datenbanktreiber (Abschnitt 3.1) — Verbindungsaufbau
ist teuer, also nicht bei jedem Klick neu.

**Die Übersetzung an der Grenze:**

```python
_API_TO_UI = {
    "werk_name": "werk",      # die API nennt es anders als die Tabelle
    ...                       # werk_id und preis stehen nicht drin: nicht gebraucht
}
```

Zwei Effekte. Erstens musste beim Umbau keine einzige Filter- oder Callback-Datei
angefasst werden. Zweitens — und wichtiger: **Ein neues Feld in der API kann das
Dashboard nie brechen**, weil nur übernommen wird, was in dieser Tabelle steht.
Das ist der Grund, warum ein hinzugefügtes Feld nur eine Unterversion ist.

#### Für das nächste Dashboard

1. `data/api_client.py` kopieren.
2. Ein `repository.py` schreiben: Produkt und Version als Konstanten, eine
   Abbildung von API-Feldern auf die eigenen Spalten, `get_materials()`-Äquivalent
   mit kurzem Cache.
3. Welche Produkte es gibt, zeigt `GET /api/v1/catalog`; welche Felder eine
   Version liefert, `GET /api/v1/catalog/<name>`.
4. Version **fest** verdrahten, nicht `latest` — ein Versionswechsel soll im
   Git-Diff auftauchen.

---

## Teil 6 — Glossar

| Begriff | In einem Satz |
|---|---|
| **Route** | Zuordnung „diese Adresse → diese Funktion". |
| **Router** | Eine Gruppe zusammengehöriger Routen. Wir haben einen pro Thema. |
| **Endpunkt** | Die Funktion, die eine Route ausführt. |
| **Dependency** | Etwas, das FastAPI dem Endpunkt vor dem Aufruf bereitstellt (Session, Aufrufer, Einstellungen). Vorteil: im Test austauschbar. |
| **Dependency Injection** | Das Prinzip dahinter: eine Funktion holt sich ihre Hilfsmittel nicht selbst, sie bekommt sie gereicht. |
| **Lifespan** | Code, der einmal beim Start und einmal beim Beenden läuft — für alles Langlebige. |
| **Middleware** | Code, der *jeden* Request umschließt (bei uns: Request-ID, Zeitmessung). |
| **Treiber / Engine** | Das Objekt, das den Vorrat an Datenbankverbindungen hält. Eines pro Programm. |
| **Session** | Eine ausgeliehene Verbindung für eine Arbeitseinheit. Eine pro Request. |
| **Pool** | Der Vorrat offener Verbindungen. |
| **Repository** | Die Schicht, die als einzige Datenbankabfragen enthält. |
| **Protocol** | Eine Beschreibung „welche Methoden muss so ein Objekt haben" — ohne Vererbung. Ermöglicht Austausch (echt ↔ Test). |
| **Pydantic-Modell** | Eine Klasse, die Felder und Typen beschreibt. Daraus entstehen Prüfung *und* Dokumentation. |
| **Schema** | Die Beschreibung eines Datensatzes (bei uns = das Pydantic-Modell). |
| **OpenAPI** | Das Standardformat, in dem eine API sich selbst beschreibt. Erzeugt `/docs`. |
| **Envelope / Umschlag** | Unser Antwortformat: `meta` (Metadaten) + `data` (die Zeilen). |
| **ETag** | Fingerabdruck einer Antwort; erlaubt „hat sich was geändert?" ohne Datenübertragung. |
| **TTL** | *Time to live* — wie lange ein zwischengespeicherter Wert gilt. |
| **async / await** | Schreibweise für „hier wird gewartet, jemand anders darf weiterarbeiten". |
| **Event Loop** | Der Kreislauf, der die wartenden Aufgaben verwaltet. |
| **ORM** | Bibliothek, die Datenbankzeilen auf Objekte abbildet. Nutzen wir nur für die Schreibseite. |
| **Migration** | Ein versioniertes Skript, das das Datenbankschema ändert. |
| **Idempotent** | Zweimal ausführen hat dasselbe Ergebnis wie einmal. Gilt für `PUT`, `PATCH`, `DELETE` — nicht für `POST`. |
| **Deprecated** | Noch benutzbar, aber es gibt einen Nachfolger; wird zu einem Stichtag abgeschaltet. |
| **Sunset** | Ebendieser Stichtag. |

---

## Teil 7 — Was du nicht wissen musst

Damit die Liste oben nicht abschreckend wirkt — für den Alltag reicht deutlich
weniger. Wenn du ein Datenprodukt anlegst, brauchst du genau vier Dinge:

1. **Ein Pydantic-Modell** für die Felder, die rauskommen sollen.
2. **Ein Params-Modell** für die Filter, die reingehen dürfen.
3. **Eine `transform()`-Funktion** — normales Python, keine Datenbank, kein HTTP.
4. **Eine `load()`-Funktion** mit drei Zeilen Verdrahtung.

Der Rest — Routing, Validierung, Dokumentation, Caching, ETag, Fehlerformat,
Paginierung, Berechtigungsprüfung — passiert automatisch. Das war der Zweck der
ganzen Konstruktion: dass man beim Hinzufügen eines Datenprodukts über nichts
davon nachdenken muss.

Das Rezept dazu steht in
[`api_development_guide.md`](api_development_guide.md#6-recipe-add-a-data-product),
die Begründungen im Detail in [`api_layer_concept.md`](api_layer_concept.md).

---

## Weiterlesen

* [FastAPI-Tutorial](https://fastapi.tiangolo.com/tutorial/) — sehr gut geschrieben, viele lauffähige Beispiele.
* [Pydantic-Dokumentation](https://docs.pydantic.dev/) — vor allem „Models" und „Validators".
* [MDN: HTTP-Grundlagen](https://developer.mozilla.org/de/docs/Web/HTTP) — Methoden, Statuscodes, Caching.
* Roy Fielding, *Architectural Styles and the Design of Network-based Software Architectures* (2000) — die Dissertation, in der REST beschrieben wurde. Kapitel 5 ist der relevante Teil.
