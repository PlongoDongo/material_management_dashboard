-- Mock-Lieferhistorie fuer das Datenprodukt `supplier-risk`.
--
--   psql "postgresql://user:passwort@host:5432/analytics" -f seed/seed_postgres.sql
--
-- Struktur passend zu _DELIVERIES_SQL in
-- src/data_api/repositories/deliveries.py.
--
-- Die Tabelle heisst bewusst `lieferungen` und liegt im Schema `mock`, damit
-- sie sich spaeter rueckstandsfrei entfernen laesst:
--     DROP SCHEMA mock CASCADE;
-- Bis dahin macht `search_path` sie unter ihrem einfachen Namen sichtbar.

CREATE SCHEMA IF NOT EXISTS mock;

DROP TABLE IF EXISTS mock.lieferungen;
CREATE TABLE mock.lieferungen (
    id            bigserial PRIMARY KEY,
    lieferant_id  text        NOT NULL,
    material_nr   text        NOT NULL,
    zugesagt_am   date        NOT NULL,
    geliefert_am  date        NOT NULL,
    menge         integer     NOT NULL CHECK (menge > 0),
    reklamationen smallint    NOT NULL DEFAULT 0 CHECK (reklamationen >= 0)
);

-- Der Index deckt genau den Zugriff des Datenprodukts ab:
-- WHERE geliefert_am >= :seit  ORDER BY lieferant_id, geliefert_am
CREATE INDEX lieferungen_seit_idx ON mock.lieferungen (geliefert_am, lieferant_id);

-- 40 Lieferungen je Lieferant, mit unterschiedlichem Verzugsprofil, damit der
-- Risiko-Score sichtbar streut: L-001 puenktlich ... L-003 deutlich verspaetet.
INSERT INTO mock.lieferungen (lieferant_id, material_nr, zugesagt_am, geliefert_am,
                              menge, reklamationen)
SELECT
    lieferant.id,
    'MAT-' || (100777 + (floor(random() * 64)::int * 13)),
    zugesagt,
    zugesagt + (greatest(0, round(lieferant.verzug_bias + random() * 6 - 3))::int),
    10 + floor(random() * 890)::int,
    CASE WHEN random() < 0.03 THEN 2 WHEN random() < 0.15 THEN 1 ELSE 0 END
FROM (VALUES ('L-001', 0), ('L-002', 2), ('L-003', 6), ('L-004', 1))
         AS lieferant(id, verzug_bias),
     generate_series(0, 39) AS i,
     LATERAL (SELECT DATE '2026-01-01' + (i * 5)) AS d(zugesagt);

-- Die API fragt `FROM lieferungen` ab, also ohne Schema. Eine View im
-- Standardschema macht die Mock-Tabelle unter diesem Namen sichtbar, ohne dass
-- die Query angepasst werden muss.
--
-- Zum Ausbauen spaeter genau zwei Zeilen:
--     DROP VIEW public.lieferungen;
--     DROP SCHEMA mock CASCADE;
-- Danach zeigt `lieferungen` auf die echte Tabelle.
CREATE OR REPLACE VIEW public.lieferungen AS SELECT * FROM mock.lieferungen;

SELECT lieferant_id,
       count(*)                                        AS lieferungen,
       round(avg(geliefert_am - zugesagt_am), 2)       AS mittlerer_verzug
FROM   mock.lieferungen
GROUP  BY lieferant_id
ORDER  BY lieferant_id;
