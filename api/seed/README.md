# Seed data

Mock data for sources that do not exist yet belongs **in the database** -- not in
the API. Advantages over sample data in the application code:

* The API stays lean: one code path, no switches, no safety net.
* The **real** path gets exercised -- Cypher, driver, session, type conversion.
  Sample data in code skips exactly the places that break later.
* Removing it means deleting nodes, not rewriting code. Every seeded node
  carries the `:Mock` label for that purpose.

## Neo4j

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_AUTH=neo4j/password
python seed/seed_neo4j.py            # create
python seed/seed_neo4j.py --purge    # delete only (everything labelled :Mock)
```

Creates 64 `:Material` nodes, the matching `:Warengruppe` and `:Werk` nodes, and
4 `:Lieferant` nodes with `SUPPLIES` edges -- matching the queries in
`src/data_api/products/catalog/`.

> The node labels and property names are German because they mirror the graph
> model, not the code. The Cypher aliases (`RETURN m.nr AS material_number`) are
> where that vocabulary meets the English API contract.

## Postgres

```bash
psql "$POSTGRES_DSN_PSQL" -f seed/seed_postgres.sql
```

Creates the `deliveries` table and fills it with roughly 160 rows -- matching
`SQL` in `src/data_api/products/catalog/supplier_risk_v2.py`.

> Note: `psql` needs a plain DSN (`postgresql://...`), while the API expects
> `postgresql+asyncpg://...`. Same server, different notation.

## When the real source arrives

```cypher
MATCH (n:Mock) DETACH DELETE n
```

Then load the real data. Nothing in the API code changes.
