-- Flags exact-copy rows in philgeps.parquet. No rows are removed.
-- Run from data/philgeps:  duckdb < philgeps-clean.sql
--
-- Outputs:
--   philgeps_clean.parquet  every source row, plus:
--     dup_group           md5 of the record content (rows identical in every column except id share it)
--     dup_rank            1 for the first row in its group, 2+ for copies (ordered by the source id)
--     dup_count           number of rows in the group
--     counted             whether the row counts toward totals (see the rule below)
--     award_date_suspect  award_date before 2000 or more than a year in the future
--   dup_groups.parquet      one row per group that has copies, for review
--
-- Background: bettergovph/transparency-dashboard#6

CREATE TABLE keyed AS
SELECT md5(to_json([
         reference_id, contract_no, award_title, notice_title, awardee_name,
         organization_name, area_of_delivery, business_category,
         printf('%.4f', contract_amount),
         strftime(award_date, '%Y-%m-%d %H:%M:%S'),
         award_status
       ])) AS dup_group,
       *
FROM 'philgeps.parquet';

CREATE TABLE clean AS
SELECT *,
       row_number() OVER (PARTITION BY dup_group ORDER BY id) AS dup_rank,
       count(*) OVER (PARTITION BY dup_group) AS dup_count
FROM keyed;

-- Counting rule: the only place that decides whether a row counts toward totals.
-- Every row counts for now, which matches current totals, until maintainers decide
-- whether copies are real line items (#6). Proposed rule, not enabled:
--   dup_rank = 1 OR NOT (dup_count >= 10 AND reference_id IS NULL)
CREATE MACRO counting_rule(dup_rank, dup_count, reference_id) AS true;

COPY (
  SELECT *,
         counting_rule(dup_rank, dup_count, reference_id) AS counted,
         award_date < DATE '2000-01-01' OR award_date > current_date + INTERVAL 1 YEAR AS award_date_suspect
  FROM clean
  ORDER BY dup_group, dup_rank
) TO 'philgeps_clean.parquet' (FORMAT PARQUET);

COPY (
  SELECT dup_group,
         any_value(dup_count) AS copies,
         any_value(contract_amount) AS contract_amount,
         (any_value(dup_count) - 1) * any_value(contract_amount) AS amount_in_extra_copies,
         any_value(reference_id) IS NOT NULL AS has_reference_id,
         any_value(awardee_name) AS awardee_name,
         any_value(organization_name) AS organization_name,
         any_value(award_date) AS award_date,
         any_value(award_title) AS award_title
  FROM clean
  WHERE dup_count > 1
  GROUP BY dup_group
  ORDER BY amount_in_extra_copies DESC
) TO 'dup_groups.parquet' (FORMAT PARQUET);

-- Summary
SELECT count(*) AS rows_in,
       (SELECT count(*) FROM 'philgeps_clean.parquet') AS rows_out,
       count(DISTINCT dup_group) FILTER (WHERE dup_count > 1) AS dup_groups,
       count(*) FILTER (WHERE dup_rank > 1) AS extra_copies,
       sum(contract_amount) FILTER (WHERE dup_rank > 1) AS amount_in_extra_copies
FROM clean;

SELECT count(*) FILTER (WHERE NOT counted) AS rows_not_counted,
       coalesce(sum(contract_amount) FILTER (WHERE NOT counted), 0) AS amount_not_counted,
       count(*) FILTER (WHERE award_date_suspect) AS suspect_dates
FROM 'philgeps_clean.parquet';

-- What the proposed rule would exclude
SELECT count(*) AS rows_proposed_rule_would_exclude,
       sum(contract_amount) AS amount_proposed_rule_would_exclude
FROM clean
WHERE NOT (dup_rank = 1 OR NOT (dup_count >= 10 AND reference_id IS NULL));
