# Contractor entity matching: methodology and disclaimer

This describes how awardee names in the PhilGEPS data are grouped into contractor entities, what evidence each grouping rests on, and the limits of the result. For how to review and rerun, see [README.md](README.md). Background: [#6](https://github.com/bettergovph/transparency-dashboard/issues/6).

## Disclaimer

- **These groupings are an analytical aid, not a legal determination.** They're inferred from how names are spelled in procurement records and from public data. They don't establish who owns or controls a business. The SEC, DTI, PCAB and the businesses themselves are the authoritative sources.
- **Most decisions are provisional.** A decision marked `provisional` was set from automated evidence and hasn't been checked by a person. Only decisions marked `confirmed` have been. The outputs carry this status (`entity_alias.reviewed`, `entity_relation.status`), and anything shown publicly should show it too.
- **"Related" and "branch" links describe names, not conduct.** A `related` link means the names appear to be separate registrations of the same owner or group, based on name similarity and shared buyers. It doesn't imply any wrongdoing, conflict of interest or coordination. Provisional `related` links should not be shown publicly as fact until someone has checked them, e.g. shared officers in the SEC General Information Sheet.
- **Errors are possible both ways.** Two different businesses may be wrongly merged, and one business may remain split across spellings. The method favors leaving names separate when evidence is unclear, because a wrong merge misstates two businesses' totals.
- **Amounts come from the source as published.** `philgeps.parquet` contains exact-copy rows (see #6), so totals in `entity_summary.csv` include those copies and are used here for ranking only.
- **Raw names are never changed.** Every original spelling stays in the data; the entity mapping sits alongside it.
- **Corrections:** anyone can propose one by editing `decisions/awardee_decisions.csv` in a pull request, or by opening an issue with the names and the evidence.

## What counts as a match

Two names are merged when they are **the same registered business**:

- spelling variants of one name (punctuation, case, abbreviations, `&AMP;` for `&`)
- the same name with or without a legal-form word (`EGB CONSTRUCTION` / `EGB CONSTRUCTION CORPORATION`) when the evidence shows one registration
- a documented rename, e.g. a sole proprietorship that incorporated. DPWH keeps one contractor ID across these and records them as `NAME (FORMERLY: OLD NAME)`.

Names are **not** merged when they're separate registrations, even with the same owner: e.g. a DTI-registered sole proprietorship and an SEC-registered corporation that both still operate. Those can be linked as `related`, which doesn't combine totals.

Branches of one business (`ARMSCOR SHOOTING CENTER, INC.-MAKATI BRANCH`) or units of one institution (`UNIVERSITY OF THE PHILIPPINES - DILIMAN`) keep their own entities and are linked to the parent as `branch_of`, so totals can be rolled up to the parent.

## Data

| Source | Version | Used for |
|---|---|---|
| `philgeps.parquet`, [bettergovph/philgeps-data](https://huggingface.co/datasets/bettergovph/philgeps-data) | uploaded 2025-11-16; 5,481,161 rows | awardee names, buyers, years, amounts |
| `dpwh_transparency_data.parquet`, [bettergovph/dpwh-transparency-data](https://huggingface.co/datasets/bettergovph/dpwh-transparency-data) | updated 2026-01-22; 248,220 contracts | DPWH contractor registrations ("NAME (ID)") |

## Steps

### 1. Normalize names (`normalize.py`, unit-tested in `test_normalize.py`)

Each name gets three keys, from strict to loose:

| Key | What's ignored | Used for |
|---|---|---|
| `clean_key` | case, punctuation, spacing; HTML entities (`&AMP;`, `&NBSP;`); accents (`Ñ` → `N`); initials (`J.T.` = `JT`); common abbreviations (`DEV'T`, `CONST.`, `GEN. MDSE.`, `PHILS.`, `CORPORATION` → `CORP`); notes such as `(FORMERLY: ...)` and `DOING BUSINESS UNDER THE NAME AND STYLE OF ...` | automatic merge |
| `core_key` | also trailing legal forms: INC, CORP, CO, LTD, OPC | review candidate |
| `loose_key` | also trailing business types: TRADING, ENTERPRISES, GENERAL MERCHANDISE, SUPPLY, SUPPLIES, SERVICES | review candidate |

Numbers are never ignored (`729 CONSTRUCTION` ≠ `722 CONSTRUCTION`), and words that may be personal names (e.g. `PHIL`) aren't expanded.

Joint ventures are detected by explicit markers (`JOINT VENTURE`, `JOIN VENTURE`, `J.V.`, `JV` after `-`, `/`, `(` or a space, `CONSORTIUM`, excluding known false positives such as `LAYSON JV & CO., INC.`) and **left out** of this step. They're handled separately, since a JV is its own legal awardee.

### 2. Merge spellings automatically

Names with the same `clean_key` are merged without review: 127,049 names → 124,038 (1,871 groups of spellings), written to `out/auto_merges.csv`.

### 3. Propose candidate pairs (`match_awardees.py`)

Groups from step 2 are paired when they share a `core_key` (`legal_form`) or a `loose_key` (`business_type`), or when their `core_key`s are similar (`fuzzy`: difflib ratio ≥ 0.9, compared within blocks of names that share a first word or first five letters). Pairs whose names contain different numbers are dropped.

Only pairs where one side has at least ₱1B in contracts are kept for this pilot. On the 2025-11-16 data this gives 355 pairs (131 legal form, 71 business type, 153 fuzzy). Names above ₱1B hold about 76% of all contract value.

### 4. Gather evidence for each pair

| Evidence | How it's computed | What it suggests |
|---|---|---|
| Shared buyers | buying organizations that awarded contracts to both names | many shared buyers: same business; none (for a fuzzy pair): likely different |
| Active years | first and last award year per name | one name ending as the other starts: a rename |
| Notes in the name | `(FORMERLY: ...)` etc. removed in step 1 | direct statement of a rename |
| DPWH contractor ID | PhilGEPS rows whose title starts with a DPWH contract ID (e.g. `24F00470`) are joined to DPWH's record of that contract. DPWH lists the contractor as `NAME (ID)`, and joint ventures as several of those. Each awardee is matched to its own entry by comparing only the distinctive words of the names (so `STO CRISTO CONSTRUCTION AND TRADING` isn't matched to `CHOLO CONSTRUCTION AND TRADING`), at a similarity of at least 0.85. | same ID: same registration. Different IDs: separate registrations. A name linked to two or more IDs: ambiguous. |

From this, `suggested_decision` is `yes` for the same DPWH ID, `no` for different DPWH IDs, `no` for a fuzzy pair with no shared buyers, and blank otherwise.

### 5. Triage (`next_action`)

A reviewer triage file (`awardee_match_review_related.csv`, 2026-10-26) added further columns to the 355 pairs: `name_core_a/b` (distinctive part of each name), `core_similarity`, `buyer_overlap` (shared buyers as a share of the smaller side's buyers), `year_handoff` (one name's activity ending as the other's starts), `evidence_score` (0–10), `relation_label`, `relation_evidence` and `next_action`. These columns were produced in a separate AI-assisted (Claude) analysis session, not by this repository's scripts, so they can't yet be reproduced from the code here. Only `next_action` is used, as the starting point for decisions, and every decision taken from it is `provisional` until a person confirms it.

### 6. Turn triage into decisions (`review_decisions.py import`)

Each `next_action` becomes a **provisional** decision in `decisions/awardee_decisions.csv`:

| `next_action` | Decision |
|---|---|
| merge | `merge` (all 108 are pairs with the same DPWH contractor ID) |
| merge; confirm with SEC/DTI lookup if amount is material | `merge` if the amount at stake is under ₱1B and DPWH doesn't link the names to different IDs; otherwise `pending` |
| keep separate | `separate` |
| keep separate; pull GIS/DTI owner before linking | `separate`, noted as a possible related party |
| keep separate; link as related; pull GIS for shared officers | `related`, noted to verify shared officers |
| keep units separate; roll up to parent for totals | `branch`, with `parent_name` set to the shared start of the two names (e.g. `UNIVERSITY OF THE PHILIPPINES`) |
| registry lookup on both names | `pending` |

Two adjustments to the triage are made by rule:
- "merge; confirm ... if material" pairs with ₱1B or more at stake, or where DPWH links one of the names to more than one contractor ID (e.g. `CAGALAWAN ENTERPRISES (38019)` and `CAGALAWAN ENTERPRISES INC. (51349)`), wait for a registry lookup.
- Pairs kept separate whose names are near-identical (core similarity ≥ 0.9) and share at least 3 buyers are noted as a possible typo of one name (e.g. `F. GURREA` / `F. GURRREA`), for a reviewer to look at. They stay separate until then.

Result: 192 merge, 106 separate, 48 pending, 4 related, 5 branch, all provisional.

### 7. Build the mapping (`build_seeds.py`)

Automatic merges and `merge` decisions are joined transitively into entities. `related` and `branch` decisions become relations. `pending` and `separate` add nothing. The build stops if decisions contradict each other (e.g. A = B and B = C merged, but A–C marked separate). The canonical name of an entity is its spelling with the largest contract amount, with HTML entities decoded.

On the current decisions: 2,038 multi-name entities covering 4,187 spellings, and 11 relations.

## Known limitations

- The pilot only covers pairs involving names with ₱1B or more. Smaller contractors are only grouped by step 2.
- DPWH evidence exists only for DPWH contracts whose PhilGEPS title starts with the contract ID. Most non-construction suppliers have none.
- Fuzzy matching compares spelling only. Two genuinely different businesses with similar names (`THREE W BUILDERS` / `THREE G BUILDERS`) can be proposed; shared buyers and DPWH IDs are what separate them.
- Joint ventures aren't yet linked to their member contractors.
- `entity_id` is derived from the canonical name, so it changes if a different spelling becomes the largest.
