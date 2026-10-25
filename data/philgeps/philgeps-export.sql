-- Exports philgeps_clean.parquet to CSV for MeiliSearch with a deterministic id.
-- Run from data/ after philgeps-clean.sql:  duckdb < philgeps/philgeps-export.sql
--
-- id = md5 of the record's content + '-' + occurrence number among identical rows
-- (dup_group and dup_rank from philgeps-clean.sql). The same parquet always produces
-- the same ids, so rebuilds don't reshuffle them. The source has no natural key
-- (reference_id is null on ~35% of rows) and some rows are exact copies of each other,
-- so the occurrence number keeps every row and keeps ids unique.
-- The id shipped in the parquet is kept as source_id.

COPY (
  SELECT dup_group || '-' || dup_rank AS id,
         id AS source_id,
         * EXCLUDE (id, dup_group)
  FROM 'philgeps/philgeps_clean.parquet'
  ORDER BY dup_group, dup_rank
) TO 'philgeps/philgeps.csv' (HEADER, DELIMITER ',');
