# Seed-Daten

Mock-Daten fuer Quellen, die es noch nicht gibt, gehoeren **in die Datenbank** --
nicht in die API. Vorteile gegenueber Beispieldaten im Anwendungscode:

* Die API bleibt schlank: ein Codepfad, keine Schalter, kein Sicherheitsnetz.
* Der komplette echte Weg wird geuebt -- Cypher, Treiber, Session, Typen.
  Beispieldaten im Code ueberspringen genau die Stellen, an denen es spaeter knallt.
* Zum Ausbauen loescht man Knoten, statt Code umzubauen. Jeder Seed-Knoten
  traegt dafuer das Label `:Mock`.

## Neo4j

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_AUTH=neo4j/passwort
python seed/seed_neo4j.py            # anlegen
python seed/seed_neo4j.py --purge    # nur loeschen (alles mit Label :Mock)
```

Erzeugt 64 `:Material`, die zugehoerigen `:Warengruppe`- und `:Werk`-Knoten und
4 `:Lieferant` mit `SUPPLIES`-Kanten -- passend zu den Queries in
`src/data_api/repositories/materials.py`.

## Postgres

```bash
psql "$POSTGRES_DSN_PSQL" -f seed/seed_postgres.sql
```

Legt die Tabelle `lieferungen` an und fuellt sie mit ca. 160 Zeilen -- passend
zur Query in `src/data_api/repositories/deliveries.py`.

> Hinweis: `psql` braucht einen normalen DSN (`postgresql://...`), waehrend die
> API `postgresql+asyncpg://...` erwartet. Gleicher Server, andere Notation.

## Wenn die echte Quelle kommt

```cypher
MATCH (n:Mock) DETACH DELETE n
```

Danach die echten Daten laden. Am API-Code aendert sich nichts.
