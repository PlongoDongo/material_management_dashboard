-- Mock delivery history for the `supplier-risk` data product.
--
--   psql "postgresql://user:password@host:5432/analytics" -f seed/seed_postgres.sql
--
-- The structure matches SQL in
-- src/data_api/products/catalog/supplier_risk_v2.py.
--
-- The table lives in the `mock` schema so it can be removed without a trace:
--     DROP SCHEMA mock CASCADE;
-- Until then a view in the default schema makes it visible under the plain name
-- the query uses.

CREATE SCHEMA IF NOT EXISTS mock;

DROP TABLE IF EXISTS mock.deliveries CASCADE;
CREATE TABLE mock.deliveries (
    id              bigserial PRIMARY KEY,
    supplier_id     text        NOT NULL,
    material_number text        NOT NULL,
    promised_on     date        NOT NULL,
    delivered_on    date        NOT NULL,
    quantity        integer     NOT NULL CHECK (quantity > 0),
    complaints      smallint    NOT NULL DEFAULT 0 CHECK (complaints >= 0)
);

-- The index covers exactly the access pattern of the data product:
-- WHERE delivered_on >= :since AND supplier_id = ANY(:ids)
--   ORDER BY supplier_id, delivered_on
CREATE INDEX deliveries_since_idx ON mock.deliveries (delivered_on, supplier_id);

-- 40 deliveries per supplier with different delay profiles, so the risk score
-- spreads visibly: L-001 punctual ... L-003 clearly late.
INSERT INTO mock.deliveries (supplier_id, material_number, promised_on, delivered_on,
                             quantity, complaints)
SELECT
    supplier.id,
    'MAT-' || (100777 + (floor(random() * 64)::int * 13)),
    promised,
    promised + (greatest(0, round(supplier.delay_bias + random() * 6 - 3))::int),
    10 + floor(random() * 890)::int,
    CASE WHEN random() < 0.03 THEN 2 WHEN random() < 0.15 THEN 1 ELSE 0 END
FROM (VALUES ('L-001', 0), ('L-002', 2), ('L-003', 6), ('L-004', 1))
         AS supplier(id, delay_bias),
     generate_series(0, 39) AS i,
     LATERAL (SELECT DATE '2026-01-01' + (i * 5)) AS d(promised);

-- The API queries `FROM deliveries`, without a schema. A view in the default
-- schema exposes the mock table under that name without changing the query.
--
-- To remove it later, exactly two statements:
--     DROP VIEW public.deliveries;
--     DROP SCHEMA mock CASCADE;
-- After that `deliveries` points at the real table.
CREATE OR REPLACE VIEW public.deliveries AS SELECT * FROM mock.deliveries;

SELECT supplier_id,
       count(*)                                     AS deliveries,
       round(avg(delivered_on - promised_on), 2)    AS avg_delay
FROM   mock.deliveries
GROUP  BY supplier_id
ORDER  BY supplier_id;
