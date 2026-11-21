-- Index comparison exercise on real + scaled data (100k synthetic rows).
-- Run in a disposable database after loading the anonymized roll.
-- The transaction rolls back at the end so no permanent data is modified.

BEGIN;

CREATE TEMP TABLE voter_perf AS SELECT * FROM voter WITH NO DATA;
INSERT INTO voter_perf SELECT * FROM voter;

INSERT INTO voter_perf(
    epic, part_no, section_no, serial_no, name,
    relation_type, relation_name, house_no, age, gender, status, updated_at
)
SELECT
    'SYN' || lpad(g::text, 8, '0'),
    271,
    1,
    1000 + g,
    'Synthetic ' || g,
    'Others',
    'Synthetic',
    'S-' || g,
    18 + (g % 100),
    CASE WHEN g % 2 = 0 THEN 'M' ELSE 'F' END,
    'PENDING'::voter_status,
    now()
FROM generate_series(1, 100000) AS g
ON CONFLICT DO NOTHING;

ANALYZE voter_perf;

-- Baseline without index (sequential scan)
DROP INDEX IF EXISTS voter_perf_name_idx;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM voter_perf WHERE name = 'Synthetic 99999';

-- With B-tree index on name (index scan)
CREATE INDEX voter_perf_name_idx ON voter_perf(name);
ANALYZE voter_perf;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM voter_perf WHERE name = 'Synthetic 99999';

ROLLBACK;
