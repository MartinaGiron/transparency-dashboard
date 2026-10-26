# PhilGEPS contractor entities

Groups awardee name spellings that belong to the same contractor (e.g. `EGB CONSTRUCTION` and `EGB CONSTRUCTION CORPORATION`), so totals can be shown per contractor instead of per spelling. Raw names are never changed; the mapping sits alongside them. Background: [#6](https://github.com/bettergovph/transparency-dashboard/issues/6).

**Read [METHODOLOGY.md](METHODOLOGY.md) first**: it explains how names are matched, what the evidence means, and the disclaimer that applies to the results.

## Files

| File | Who edits it | Committed |
|---|---|---|
| `decisions/awardee_decisions.csv`, `decisions/jv_decisions.csv` | **reviewers** | yes |
| `normalize.py`, `match_awardees.py`, `triage.py`, `review_decisions.py`, `jv_members.py`, `build_seeds.py` | developers | yes |
| `out/` (review file, automatic merges, entity tables) | nobody, regenerated | no |

## Running the pipeline

```bash
cd data/philgeps/entities

# 1. Find candidate pairs. Needs ../philgeps.parquet; --dpwh is optional but recommended
#    (huggingface.co/datasets/bettergovph/dpwh-transparency-data)
python match_awardees.py --input ../philgeps.parquet --dpwh dpwh_transparency_data.parquet

# 2. Add any new pairs to the decisions file. Existing decisions are never changed.
python review_decisions.py import --from out/awardee_match_review.csv

# 3. Check the decisions, then build the entity tables
python review_decisions.py check

# 4. Joint ventures: propose members, add new JV names to decisions/jv_decisions.csv
python jv_members.py propose --input ../philgeps.parquet --dpwh dpwh_transparency_data.parquet
python jv_members.py import --from out/jv_review.csv

# 5. Build the entity tables
python build_seeds.py --input ../philgeps.parquet
```

Outputs in `out/`: `entity.csv`, `entity_alias.csv`, `entity_relation.csv`, and `entity_summary.csv` (largest entities, their total vs. their largest single spelling), `entity_membership.csv` (JV members) and `contractor_jv_summary.csv` (each contractor's direct, JV "involved in" and JV "attributed" totals). Tests: `python -m unittest test_normalize test_triage test_jv_members`.

`out/awardee_match_review.csv` includes the triage columns from `triage.py` (`relation_label`, `evidence_score`, `next_action`, ...). Step 2 turns `next_action` into provisional decisions for new pairs only.

## Reviewing

Open `decisions/awardee_decisions.csv` in a spreadsheet. Each row is a pair of names. The rows are sorted by `amount_at_stake` (the smaller side's contract amount, which moves if the pair is merged), so start at the top. `evidence` summarizes why the pair was proposed. For more detail (shared buyers, years, DPWH contractor IDs) look the pair up in `out/awardee_match_review.csv`.

To record a decision, edit these columns and leave the others alone:

| Column | Values |
|---|---|
| `decision` | `merge` same registered business, including a documented rename · `separate` different businesses · `related` separate registrations of the same owner or group (linked, totals not combined) · `branch` branches or units of `parent_name` (linked, totals rolled up) · `pending` not decided (treated as separate) |
| `parent_name` | for `branch` only: the parent's name |
| `status` | `confirmed` once you've checked it yourself; `provisional` if it rests on automated evidence only |
| `basis` | what you relied on, e.g. `SEC registration CS2015-12345`, `DTI BNRS 1234567`, `PCAB license 12345`, `same DPWH contractor ID` |
| `notes`, `reviewer`, `reviewed_on` | free text, your name, the date (YYYY-MM-DD) |
| `assigned_to` | who the pair is handed to; set by `export --assign`, cleared by `apply` |

Where to start:
1. `pending` rows (48), especially those with ₱1B or more at stake. They need a registry lookup: SEC "Check with SEC" for corporations, DTI's business name search (BNRS) for sole proprietorships, PCAB's licensed contractor list for construction firms. Two registrations means `separate` (or `related`); one registration, or one listing the other name as a former name, means `merge`.
2. Rows whose `notes` mention a possible typo, or a related party to check.
3. Provisional `merge` rows: skim and mark `confirmed` the ones you agree with.

When unsure, choose `pending` or `separate`. A missed merge is harmless; a wrong one misstates two businesses' totals.

After editing, rerun steps 2–3 above. `review_decisions.py check` lists any invalid values or contradictions (e.g. A = B and B = C merged, but A–C marked separate), and `build_seeds.py` refuses to build until they're fixed.

To add a pair the matcher didn't propose, add a row with both names spelled exactly as in PhilGEPS.

## Reviewing joint ventures

`decisions/jv_decisions.csv` has one row per JV name with its `members`, separated by ` | ` and spelled as in PhilGEPS where the member is an awardee itself. `notes` and `evidence` say why a row needs a look: a member not found as a contractor, DPWH listing different members, or a JV found only by its `/`. Out of 1,169 JV names, 209 are flagged in `out/jv_review.csv` (`needs_review = yes`). Correct `members` if needed, and set `status` to `confirmed` with a `basis` (e.g. `DPWH contractor listing`, `notice of award`) and your name.

## Handing pairs to another reviewer

To give a batch (e.g. all `pending` pairs that need a registry lookup) to someone without handing out the decisions file:

```bash
# Write a worksheet with the evidence and blank answer columns, and mark the pairs as assigned
python review_decisions.py export --decision pending --assign "registry reviewer" --out pending_lookups.csv

# ...the reviewer fills in answer, registry and registration numbers, reviewer, reviewed_on...

# Bring it back, then check and rebuild
python review_decisions.py apply --from pending_lookups.csv
python review_decisions.py check
python build_seeds.py --input ../philgeps.parquet
```

`apply` only updates pairs that are still `pending`, and never overwrites a decision made in the meantime. An answer becomes `confirmed` when it has a registration number and a reviewer name; otherwise it's applied as `provisional`. `unsure` keeps the pair pending, with the reviewer's note added. Invalid answers are reported and skipped. If the answers would contradict other decisions, nothing is written.

While a batch is out, `assigned_to` shows who has it. Assigned pairs stay `pending`, which the build treats as separate.

## Output format

- `entity.csv`: `entity_id, canonical_name, entity_type, uacs_code`
- `entity_alias.csv`: `raw_name, source, entity_id, match_method, confidence, reviewed`. `match_method` is `normalized` (automatic), `evidence` (provisional decision) or `reviewed` (confirmed decision).
- `entity_relation.csv`: `entity_id, related_entity_id, relation, status, basis`. `relation` is `related` or `branch_of` (pointing to the parent).
- `entity_membership.csv`: `parent_entity_id` (the JV), `member_entity_id`, `share`, `evidence`.
- `contractor_jv_summary.csv`: `direct_total`, `jv_involved_total` (not additive across contractors), `jv_attributed_total`.

Only names in a multi-name entity or a relation are listed; any other `awardee_name` is its own entity.
