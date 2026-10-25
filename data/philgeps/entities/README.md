# PhilGEPS contractor entities

Groups awardee name spellings that belong to the same contractor (e.g. `EGB CONSTRUCTION` and `EGB CONSTRUCTION CORPORATION`), so totals can be shown per contractor instead of per spelling. Raw names are never changed; the mapping sits alongside them. Background: [#6](https://github.com/bettergovph/transparency-dashboard/issues/6).

## Steps

```bash
cd data/philgeps/entities

# 1. Find candidates (needs ../philgeps.parquet)
python match_awardees.py --input ../philgeps.parquet --min-amount 1e9

# 2. Review out/awardee_match_review.csv: set `decision` to yes / no / unsure for each pair

# 3. Build the mapping from automatic merges plus "yes" pairs
python build_seeds.py --matches out --review out/awardee_match_review.csv --out-dir out
```

Outputs go to `out/` (not committed). Running the tests:

```bash
python -m unittest test_normalize
```

## How names are matched

`normalize.py` gives each name three keys:

| Key | Ignores | Same key means |
|---|---|---|
| `clean_key` | case, punctuation, spacing, HTML entities (`&AMP;`), accents, initials (`J.T.` = `JT`), common abbreviations (`DEV'T`, `GEN. MDSE.`), notes like `(FORMERLY: ...)` | same name, merged automatically |
| `core_key` | also trailing legal forms: INC, CORP, CO, LTD, OPC | probably the same company, reviewed |
| `loose_key` | also trailing business types: TRADING, ENTERPRISES, GENERAL MERCHANDISE, SUPPLY, SERVICES | possibly related, reviewed |

`match_awardees.py` also proposes fuzzy pairs (similar `core_key`). Names whose numbers differ (`729 CONSTRUCTION` vs `722 CONSTRUCTION`) are never paired. Joint ventures are left out and handled separately.

The review file is sorted by the amount that would move if a pair is merged. Its evidence columns help decide: how many buying organizations the two names share, each name's active years, and any notes cut from the name. Precision matters more than recall here: wrongly merging two real contractors is worse than leaving a variant unmerged, so mark anything doubtful `no` or `unsure`.

## Output format

- `entity.csv`: `entity_id, canonical_name, entity_type, uacs_code`
- `entity_alias.csv`: `raw_name, source, entity_id, match_method, confidence, reviewed`

Only names belonging to a multi-name entity are listed; any other `awardee_name` is its own entity.
