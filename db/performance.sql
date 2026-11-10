-- Run after importing a copy of the roll. Measure a lookup before and after an index.
-- The initial 494-row relation may prefer a sequential scan. Synthetic rows expand
-- the test relation to 100k; perform this in a disposable database only.
BEGIN;
CREATE TEMP TABLE voter_perf AS SELECT * FROM voter WITH NO DATA;
INSERT INTO voter_perf SELECT * FROM voter;
INSERT INTO voter_perf(epic,part_no,section_no,serial_no,name,relation_type,relation_name,house_no,age,gender,status)
SELECT 'SYN'||lpad(g::text,8,'0'),271,1,1000+g,'Synthetic '||g,'Others','Synthetic','S-'||g,18+(g%100),
       CASE WHEN g%2=0 THEN 'M' ELSE 'F' END,'PENDING'::voter_status,now() FROM generate_series(1,100000) g
ON CONFLICT DO NOTHING;
ANALYZE voter_perf;
DROP INDEX IF EXISTS voter_perf_name_idx;
EXPLAIN (ANALYZE,BUFFERS) SELECT * FROM voter_perf WHERE name='Synthetic 99999';
CREATE INDEX voter_perf_name_idx ON voter_perf(name);
ANALYZE voter_perf;
EXPLAIN (ANALYZE,BUFFERS) SELECT * FROM voter_perf WHERE name='Synthetic 99999';
ROLLBACK;
