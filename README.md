# Material Management Dashboard (Plotly Dash)

Lauffähiges Gerüst für das neue Dashboard, aufgebaut nach dem Claude-Design-Mockup.
Enthält die vollständige Filter- und Tab-Mechanik mit synthetischen Polars-Mock-Daten.
Der Neo4j-Anschluss ist auf **eine** Datei isoliert (`data/repository.py`).

## Schnellstart

```bash
pip install -r requirements.txt
python app.py            # -> http://127.0.0.1:8050
pytest -q                # Filter- und KPI-Logik
```

## Projektstruktur

```
material_management_dashboard/
├── app.py                     # Einstiegspunkt: Top-Level-Layout + Callback-Registrierung
├── config.py                  # Farben, Element-IDs (eine Wahrheit), Tab-Liste
├── assets/style.css           # Design (Dash lädt assets/ automatisch)
│
├── components/                # Geteiltes "Chrome"
│   ├── header_layout.py       #   Team-Header (Restriction|Burger|Logo|Titel|Filter) -> WIEDERVERWENDBAR
│   ├── header_callbacks.py    #   Sidebar-Toggles                            -> WIEDERVERWENDBAR
│   ├── nav_sidebar.py         #   linke Navigations-Sidebar (Inhalt = Python-Liste)
│   ├── filter_sidebar.py      #   rechte Filter-Sidebar (Wahrheitsquelle der Filter)
│   └── footer_tabs.py         #   blaue Footer-Tab-Leiste
│
├── tabs/                      # Tab-Inhalte
│   ├── data_overview.py       #   Tab 1: KPI-Kacheln + Materialtabelle
│   ├── manage_data.py         #   Tab 2: Platzhalter
│   └── apply_mappings.py      #   Tab 3: Platzhalter
│
├── data/
│   ├── repository.py          # Datenzugriff: Mock-Polars-DF  <-- NEO4J-EINSTIEGSPUNKT
│   └── filtering.py           # apply_filters(df, filter_dict) -> df  (rein, testbar)
│
├── kpi/
│   └── kpi_rules.py           # regelbasierte KPI-Berechnung (rein, testbar)
│
├── callbacks/
│   ├── filter_callbacks.py    # Filter, KPI-Klick, Tabellen-Rendering
│   └── tab_callbacks.py       # Tab-Umschaltung
│
└── tests/                     # PyTest für filtering + kpi_rules
```

---

## Die zentrale Architekturentscheidung: warum Single-Page + Tabs statt Plotly Pages

Deine Kernanforderung ist: **Filter müssen über die Tabs hinweg erhalten bleiben.**
Der sauberste Weg dazu ist, dass das gesamte Gerüst (Header, beide Sidebars,
Footer, ja sogar alle Tab-Inhalte) **dauerhaft im DOM** bleibt und **nur die
Sichtbarkeit** per CSS (`display:none`) umgeschaltet wird.

```
app.layout  (wird nie neu gerendert)
├── dcc.Store(store-filters)      <- kanonischer Filterzustand, storage_type="session"
├── dcc.Store(store-active-tab)
├── Header
├── Nav-Sidebar (links)
├── Filter-Sidebar (rechts)       <- Steuerelemente bleiben immer gemountet
├── Main
│   ├── content-overview  (sichtbar)
│   ├── content-manage    (display:none)
│   └── content-mappings  (display:none)
└── Footer-Tabs
```

Weil nichts unmountet wird, gehen weder die Filter-Steuerelemente noch der
Store-Zustand beim Tab-Wechsel verloren – **ohne jeden Zusatzaufwand**. Der
Screenshot-Test bestätigt das: Klick auf „Gesperrt" → 6/64 → Tab 2 → zurück
zu Tab 1 → weiterhin 6/64, Status-Dropdown weiterhin „Gesperrt".

### Zur Korrektur deiner Annahme zu Plotly Pages

> „Das ist glaube ich nicht mit Plotly Pages möglich."

**Doch, es ist möglich** – deine Annahme stimmt so nicht ganz. Filter-Persistenz
ist **kein** Grund, Pages zu meiden. Entscheidend ist nur, **wo** der Zustand liegt:

* Mit `use_pages=True` wird bei einem Seitenwechsel ausschließlich der
  `dash.page_container` ausgetauscht. **Alles, was du im `app.layout` außerhalb
  von `page_container` platzierst, überlebt die Navigation** – inklusive eines
  `dcc.Store(storage_type="session")` und der gesamten Filter-Sidebar.
* Du müsstest dann in jeder Seite den Filter aus dem Store „rehydrieren"
  (Store als `State`/`Input` lesen), weil die *Seiteninhalte* beim Wechsel neu
  aufgebaut werden.

Kurz gesagt:

| Aspekt | Single-Page + Tabs (gewählt) | Plotly Pages (Multi-Page) |
|---|---|---|
| Filter persistieren | automatisch (nichts unmountet) | möglich, via Store im app.layout + Rehydrieren |
| Geteiltes Chrome (Header/Sidebars) | trivial, liegt im app.layout | ins app.layout um `page_container` legen |
| Bookmarkbare URLs / Back-Button | nein (ließe sich mit `dcc.Location` nachrüsten) | ja, eingebaut |
| Lazy Loading großer Module | nein | ja |
| Komplexität für deinen Fall | niedrig | höher |

**Empfehlung:** Für dieses Dashboard – geteiltes Chrome + persistente Filter,
keine Deep-Link-Anforderung – ist **Single-Page + Tabs** die einfachere und
robustere Wahl. Sobald du bookmarkbare Tabs, echtes Lazy Loading oder viele
weitgehend unabhängige Module brauchst, ist der Wechsel zu Pages sinnvoll –
dann bleibt der Filter über einen `dcc.Store` im app.layout trotzdem erhalten.

---

## Wie das Filtern funktioniert (zyklusfreier Datenfluss)

```
[Filter-Steuerelemente in der Sidebar]  ──►  store-filters  ──►  [Tabelle + Zähler]
        ▲   ▲
        │   └──── Klick auf KPI-Kachel   (setzt Status / "ohne Klass."-Flag)
        └──────── "Zurücksetzen"-Button  (leert alle Steuerelemente)
```

Die **rechte Sidebar ist die einzige Wahrheitsquelle** des Filters. KPI-Klick und
Reset schreiben *nur* in diese Steuerelemente; von dort fließt der Zustand
weiter in `store-filters` und in die Tabelle. So gibt es **keinen Rückkanal**
Store → Steuerelement und damit **keinen Callback-Zyklus**.

* **KPI-Klick → Filter:** Jede KPI trägt in `kpi/kpi_rules.py` ein `filter`-Dict.
  Der Klick-Callback liest per `dash.ctx.triggered_id`, welche Kachel geklickt
  wurde, und setzt die passenden Steuerelemente. „Gesperrt" → Status = `["Gesperrt"]`,
  „Ohne Klassifizierung" → Flag `ohne_klass = True` (andere Filterdimension!).
* **KPI-Werte** werden über den **vollständigen** Datensatz berechnet (Gesamtlage),
  unabhängig vom Tabellenfilter. Klick filtert nur die *Tabelle*. Das entkoppelt
  KPIs und Tabelle sauber. (Alternative – KPIs sollen die aktuelle Filterung
  widerspiegeln – ist ein Einzeiler: `store-filters` zusätzlich als Input der
  KPI-Berechnung.)

---

## Potentielle Probleme & wie sie hier gelöst sind

1. **Dynamisch erzeugte Komponenten + Callbacks (der klassische Dash-Stolperstein).**
   Wenn man Tab-Inhalte per Callback *neu erzeugt*, existiert das Callback-Ziel
   (die Tabelle) zeitweise nicht → „nonexistent object" bzw. verlorene Updates.
   → **Lösung:** Alle Tab-Inhalte bleiben gemountet, nur `display` wird
   umgeschaltet. Die Tabelle ist immer ein gültiges Ziel.

2. **Doppelte Outputs (KPI-Klick *und* Reset schreiben in dasselbe Feld).**
   Dash verbietet standardmäßig zwei Callbacks auf dieselbe Output-Property.
   → **Lösung:** `allow_duplicate=True` an den betroffenen Outputs
   (`filter-status.value`, `filter-ohne-klass.value`) + `prevent_initial_call=True`.

3. **Callback-Zyklen bei Zwei-Wege-Bindung.** Würde der Store zurück in die
   Steuerelemente schreiben, entstünde eine Schleife.
   → **Lösung:** Einbahnstraße (siehe Diagramm). Steuerelemente → Store → Tabelle.

4. **Filter „springen zurück" nach Tab-Wechsel.**
   → **Lösung:** Doppelt abgesichert – (a) Steuerelemente bleiben gemountet,
   (b) zusätzlich `persistence=True, persistence_type="session"`, das sogar einen
   Browser-Reload übersteht.

5. **`suppress_callback_exceptions`.** Da wir Komponenten teils dynamisch
   referenzieren, ist das in `Dash(...)` gesetzt. Kehrseite: Tippfehler in IDs
   fliegen erst zur Laufzeit auf. → **Gegenmittel:** Alle IDs zentral in
   `config.py::IDS` – Layout und Callbacks nutzen dieselben Konstanten.

6. **Performance bei großen Neo4j-Ergebnissen.** Der `DataTable` mit
   `page_size` rendert clientseitig paginiert. Bei sehr großen Datenmengen:
   `page_action="custom"` + serverseitiges Paging, oder Aggregation bereits in
   Cypher. Die Filterung liegt in Polars (schnell); der Datenabruf ist gecacht
   (`get_materials()`), damit nicht jeder Callback Neo4j erneut anfragt.

7. **Overlay blockiert Klicks bei offener Sidebar** – das ist gewollt (Modal-
   Verhalten). Klick aufs Overlay oder das ×-Icon schließt die Sidebar.

---

## Neo4j einstecken (späterer Schritt)

Nur `data/repository.py::load_materials()` anfassen: den Cypher ausführen und
einen Polars-DataFrame mit denselben Spalten (`COLUMNS`) zurückgeben. Ein
fertiges Beispiel steht als Docstring in der Funktion. Der Rest der App bleibt
unverändert, weil alles nur `get_materials()` kennt.

## Header wiederverwenden

`components/header_layout.py` nutzt die Team-Klassen (`team-header`, `panel`,
`button-icon`, `divider` …) und Material Icons (per Stylesheet-Link, in
`app.py` als `external_stylesheets`) -- Icons also über Namen
(`html.I("menu", className="material-icons-outlined")`) statt kopierter
Sonderzeichen. `header_layout(title=…, subtitle=…, logo_src=…)` ist
parametrisiert; die Öffnen/Schließen-Logik steht in `header_callbacks.py`
(`register_header_callbacks(app)`).

Der Header enthält **keine** Sidebars -- die sind eigene Komponenten
(`nav_sidebar.py`, `filter_sidebar.py`) und liegen im Top-Level-Layout. Pro
Dashboard passt man nur deren **Python-Inhalt** an (z. B.
`nav_sidebar(items=[("dashboard", "Übersicht"), …])`); Slide-in, Overlay und
Farben stehen zentral in `assets/style.css`, sodass niemand im Team CSS
anfassen muss.

Die Basis-Styles der Team-Klassen liegen in `assets/style.css` und lassen sich
von einem zentralen Team-Stylesheet überschreiben. Material Icons kommen per
CDN-Link; in abgeschotteten Umgebungen (Proxy/Offline) lässt sich die Schrift
selbst hosten (Kommentar in `app.py`).

## RBAC

Bewusst **nicht** enthalten (laut Anforderung erst später). Ansatzpunkte für
später: Auth-Middleware auf `app.server` (Flask), rollenabhängiges Ausblenden
von Tabs/Steuerelementen im `serve_layout()`, und serverseitige Filterung der
Neo4j-Query nach Berechtigung.
