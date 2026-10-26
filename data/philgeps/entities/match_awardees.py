"""
Find awardee names in philgeps.parquet that are probably the same contractor.

Writes to --out-dir:
    auto_merges.csv            names with the same clean_key (see normalize.py), merged
                               without review: only case, punctuation, spacing, HTML
                               entities and abbreviations differ.
    awardee_match_review.csv   candidate pairs for a person to accept or reject, sorted
                               by the amount that would move if the pair is merged.
                               Fill in the `decision` column with yes / no / unsure.

Candidates (each side is a group of names that share a clean_key):
    legal_form     same core_key: differ only by INC / CORP / CO / OPC / LTD
    business_type  same loose_key: also differ by TRADING / ENTERPRISES / ...
    fuzzy          similar core_key (difflib ratio >= --min-similarity) within a block
                   of names sharing a first word or first letters

Each pair also gets triage columns from triage.py (name cores, buyer overlap, year
handoff, evidence score, relation_label and next_action); next_action is what
review_decisions.py import turns into provisional decisions.

Only pairs where one side has at least --min-amount in contracts are kept. Joint
ventures are left out; they are handled separately. Amounts include duplicate rows
in the source, so they are for ranking, not reporting.

Usage (from data/philgeps/entities):
    python match_awardees.py --input ../philgeps.parquet --min-amount 1e9
"""

import argparse
import csv
import os
import re
from collections import defaultdict
from difflib import SequenceMatcher

import duckdb

from normalize import is_joint_venture, name_keys
from triage import triage

REVIEW_COLUMNS = [
    "pair_id", "match_method", "similarity",
    "name_a", "name_b", "other_spellings_a", "other_spellings_b",
    "rows_a", "amount_a", "rows_b", "amount_b", "amount_at_stake",
    "shared_organizations", "organizations_a", "organizations_b", "top_shared_organizations",
    "years_a", "years_b", "notes_a", "notes_b",
    "dpwh_contractor_a", "dpwh_contractor_b", "dpwh_evidence",
    "suggested_decision", "suggested_reason",
    "decision", "reviewer_notes",
    "name_core_a", "name_core_b", "core_similarity", "buyer_overlap", "year_handoff",
    "evidence_score", "relation_label", "relation_evidence", "next_action",
]

# DPWH contract IDs (e.g. 24F00470) at the start of a PhilGEPS award or notice title.
DPWH_CONTRACT_ID = r"^([0-9]{2}[A-Z]{1,2}[0-9]{4,5})\b"
DPWH_CONTRACTOR = re.compile(r"(.+?)\s*\((\d+)\)")

# Ignored when checking whether a DPWH contractor entry is this awardee, so that
# "STO CRISTO CONSTRUCTION AND TRADING" doesn't match "CHOLO CONSTRUCTION AND TRADING".
GENERIC_WORDS = {
    "AND", "CONSTRUCTION", "CONSTRUCTIONS", "BUILDERS", "BUILDER", "DEVELOPMENT", "TRADING",
    "SUPPLY", "SUPPLIES", "ENTERPRISES", "GENERAL", "MERCHANDISE", "SERVICES", "CONTRACTOR",
    "CONTRACTORS", "ENGINEERING", "REALTY", "INDUSTRIES", "CORP", "INC", "CO", "OPC", "LTD",
    "PHILIPPINES", "FORMERLY",
}


def distinctive(core_key):
    words = [w for w in core_key.split() if w not in GENERIC_WORDS]
    return " ".join(words) or core_key


def load_names(con, input_path):
    return con.execute(f"""
        SELECT awardee_name, count(*) AS n_rows, sum(contract_amount) AS amount,
               min(year(award_date)) AS first_year, max(year(award_date)) AS last_year
        FROM read_parquet('{input_path}')
        WHERE awardee_name IS NOT NULL
        GROUP BY awardee_name
    """).fetchall()


def build_groups(names):
    """Group raw names by clean_key. Returns {clean_key: group dict}."""
    groups = {}
    for raw, n_rows, amount, first_year, last_year in names:
        if is_joint_venture(raw):
            continue
        keys = name_keys(raw)
        if not keys["clean_key"]:
            continue
        g = groups.setdefault(keys["clean_key"], {
            "clean_key": keys["clean_key"], "core_key": keys["core_key"], "loose_key": keys["loose_key"],
            "names": [], "n_rows": 0, "amount": 0.0, "first_year": first_year, "last_year": last_year, "notes": [],
        })
        g["names"].append((raw, n_rows, amount))
        g["n_rows"] += n_rows
        g["amount"] += amount
        if first_year is not None:
            g["first_year"] = min(first_year, g["first_year"] or first_year)
            g["last_year"] = max(last_year, g["last_year"] or last_year)
        g["notes"].extend(keys["notes"])
    for g in groups.values():
        g["names"].sort(key=lambda x: -x[2])
        g["display"] = g["names"][0][0]
    return groups


def digits(key):
    return re.findall(r"\d+", key)


def candidate_pairs(groups, min_amount, min_similarity):
    """Yield (clean_key_a, clean_key_b, method, similarity), each unordered pair once."""
    seen = set()

    def emit(a, b, method, similarity):
        pair = tuple(sorted((a, b)))
        if a == b or pair in seen:
            return None
        if max(groups[a]["amount"], groups[b]["amount"]) < min_amount:
            return None
        seen.add(pair)
        return (pair[0], pair[1], method, similarity)

    by_core, by_loose = defaultdict(list), defaultdict(list)
    for key, g in groups.items():
        by_core[g["core_key"]].append(key)
        by_loose[g["loose_key"]].append(key)

    for method, index in (("legal_form", by_core), ("business_type", by_loose)):
        for members in index.values():
            if len(members) < 2:
                continue
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if digits(groups[a]["core_key"]) != digits(groups[b]["core_key"]):
                        continue
                    pair = emit(a, b, method, 1.0)
                    if pair:
                        yield pair

    blocks = defaultdict(set)
    for key, g in groups.items():
        core = g["core_key"]
        blocks["w:" + core.split()[0]].add(key)
        blocks["p:" + core.replace(" ", "")[:5]].add(key)

    for key, g in groups.items():
        if g["amount"] < min_amount or len(g["core_key"]) < 6:
            continue
        core = g["core_key"]
        neighbours = blocks["w:" + core.split()[0]] | blocks["p:" + core.replace(" ", "")[:5]]
        for other in neighbours:
            other_core = groups[other]["core_key"]
            if other == key or other_core == core or len(other_core) < 6:
                continue
            if digits(core) != digits(other_core):
                continue
            matcher = SequenceMatcher(None, core, other_core)
            if matcher.real_quick_ratio() < min_similarity or matcher.quick_ratio() < min_similarity:
                continue
            similarity = matcher.ratio()
            if similarity >= min_similarity:
                pair = emit(key, other, "fuzzy", round(similarity, 3))
                if pair:
                    yield pair


def organization_evidence(con, input_path, groups, pairs):
    """Shared buying organizations per pair, from the source rows."""
    keys = {k for a, b, _, _ in pairs for k in (a, b)}
    con.execute("CREATE TEMP TABLE name_group (awardee_name VARCHAR, clean_key VARCHAR)")
    con.executemany("INSERT INTO name_group VALUES (?, ?)",
                    [(raw, k) for k in keys for raw, _, _ in groups[k]["names"]])
    con.execute(f"""
        CREATE TEMP TABLE group_org AS
        SELECT g.clean_key, p.organization_name, sum(p.contract_amount) AS amount
        FROM read_parquet('{input_path}') p JOIN name_group g USING (awardee_name)
        GROUP BY 1, 2
    """)
    con.execute("CREATE TEMP TABLE pairs (a VARCHAR, b VARCHAR)")
    con.executemany("INSERT INTO pairs VALUES (?, ?)", [(a, b) for a, b, _, _ in pairs])
    org_counts = dict(con.execute("SELECT clean_key, count(*) FROM group_org GROUP BY 1").fetchall())
    shared = {}
    for a, b, n, top in con.execute("""
        SELECT p.a, p.b, count(*),
               string_agg(oa.organization_name, ' | ' ORDER BY oa.amount + ob.amount DESC)
        FROM pairs p
        JOIN group_org oa ON oa.clean_key = p.a
        JOIN group_org ob ON ob.clean_key = p.b AND ob.organization_name = oa.organization_name
        GROUP BY 1, 2
    """).fetchall():
        shared[(a, b)] = (n, " | ".join(top.split(" | ")[:3]))
    return org_counts, shared


def dpwh_contractors(con, input_path, dpwh_path, groups, keys):
    """DPWH contractor registrations per group, from PhilGEPS rows whose title starts
    with a DPWH contract ID. DPWH lists each contractor as "NAME (ID)", and joint
    ventures as several of those, so each awardee is matched to its own entry.
    Returns {clean_key: {(dpwh_id, dpwh_name): contracts}}."""
    rows = con.execute(f"""
        SELECT DISTINCT p.awardee_name, d.contractor
        FROM (SELECT awardee_name,
                     coalesce(nullif(regexp_extract(award_title, '{DPWH_CONTRACT_ID}', 1), ''),
                              nullif(regexp_extract(notice_title, '{DPWH_CONTRACT_ID}', 1), '')) AS contract_id
              FROM read_parquet('{input_path}')) p
        JOIN read_parquet('{dpwh_path}') d ON d.contractId = p.contract_id
    """).fetchall()
    group_of = {raw: k for k in keys for raw, _, _ in groups[k]["names"]}
    found = defaultdict(lambda: defaultdict(int))
    for raw, contractor in rows:
        key = group_of.get(raw)
        if key is None:
            continue
        entries = [(m.group(2), m.group(1).strip(" /")) for m in DPWH_CONTRACTOR.finditer(contractor or "")]
        if not entries:
            continue
        own = distinctive(groups[key]["core_key"])
        score = lambda e: SequenceMatcher(None, own, distinctive(name_keys(e[1])["core_key"])).ratio()
        best = max(entries, key=score)
        if score(best) >= 0.85:
            found[key][best] += 1
    # Keep registrations seen on at least a fifth as many contracts as the most common one.
    return {k: {e: n for e, n in v.items() if n >= max(v.values()) * 0.2} for k, v in found.items()}


def suggest(method, n_shared, dpwh_a, dpwh_b):
    """Suggested decision and reason; the reviewer confirms it in `decision`."""
    if dpwh_a and dpwh_b:
        ids_a, ids_b = {i for i, _ in dpwh_a}, {i for i, _ in dpwh_b}
        if ids_a == ids_b:
            return "same_id", "yes", "same DPWH contractor ID"
        if ids_a & ids_b:
            return "mixed_ids", "", "DPWH links one of these names to more than one contractor ID; check"
        return "different_id", "no", "different DPWH contractor IDs: separate registrations (mark related if same owner)"
    if method == "fuzzy" and n_shared == 0:
        return "none", "no", "fuzzy match with no shared buyers"
    return "none", "", ""


def describe_dpwh(entries):
    return " | ".join(f"{name} ({i})" for (i, name), _ in sorted(entries.items(), key=lambda x: -x[1])) if entries else ""


def years(g):
    if g["first_year"] is None:
        return ""
    return str(g["first_year"]) if g["first_year"] == g["last_year"] else f"{g['first_year']}-{g['last_year']}"


def other_spellings(g, limit=5):
    rest = [raw for raw, _, _ in g["names"][1:]]
    text = " | ".join(rest[:limit])
    return text + (f" | (+{len(rest) - limit} more)" if len(rest) > limit else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", default="../philgeps.parquet", help="PhilGEPS parquet file")
    parser.add_argument("--out-dir", default="out", help="Output directory")
    parser.add_argument("--min-amount", type=float, default=1e9, help="Keep pairs where one side has at least this amount")
    parser.add_argument("--min-similarity", type=float, default=0.9, help="Minimum difflib ratio for fuzzy pairs")
    parser.add_argument("--dpwh", help="Optional dpwh_transparency_data.parquet (huggingface.co/datasets/bettergovph/"
                                       "dpwh-transparency-data) for contractor-ID evidence")
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    con = duckdb.connect()
    names = load_names(con, args.input)
    groups = build_groups(names)

    with open(os.path.join(args.out_dir, "auto_merges.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["clean_key", "awardee_name", "rows", "amount"])
        merged = 0
        for key in sorted(groups):
            g = groups[key]
            if len(g["names"]) > 1:
                merged += 1
                for raw, n_rows, amount in g["names"]:
                    w.writerow([key, raw, n_rows, f"{amount:.2f}"])

    pairs = list(candidate_pairs(groups, args.min_amount, args.min_similarity))
    org_counts, shared = organization_evidence(con, args.input, groups, pairs)
    keys = {k for a, b, _, _ in pairs for k in (a, b)}
    dpwh = dpwh_contractors(con, args.input, args.dpwh, groups, keys) if args.dpwh else {}

    rows = []
    for a, b, method, similarity in pairs:
        ga, gb = groups[a], groups[b]
        if gb["amount"] > ga["amount"]:
            a, b, ga, gb = b, a, gb, ga
        n_shared, top_shared = shared.get(tuple(sorted((a, b))), (0, ""))
        evidence, suggestion, reason = suggest(method, n_shared, dpwh.get(a), dpwh.get(b))
        rows.append({
            "match_method": method, "similarity": similarity,
            "name_a": ga["display"], "name_b": gb["display"],
            "other_spellings_a": other_spellings(ga), "other_spellings_b": other_spellings(gb),
            "rows_a": ga["n_rows"], "amount_a": f"{ga['amount']:.2f}",
            "rows_b": gb["n_rows"], "amount_b": f"{gb['amount']:.2f}",
            "amount_at_stake": gb["amount"],
            "shared_organizations": n_shared,
            "organizations_a": org_counts.get(a, 0), "organizations_b": org_counts.get(b, 0),
            "top_shared_organizations": top_shared,
            "years_a": years(ga), "years_b": years(gb),
            "notes_a": " ; ".join(sorted(set(ga["notes"]))), "notes_b": " ; ".join(sorted(set(gb["notes"]))),
            "dpwh_contractor_a": describe_dpwh(dpwh.get(a)), "dpwh_contractor_b": describe_dpwh(dpwh.get(b)),
            "dpwh_evidence": evidence if args.dpwh else "", "suggested_decision": suggestion, "suggested_reason": reason,
            "decision": "", "reviewer_notes": "",
        })
    for r in rows:
        r.update(triage(r))
    rows.sort(key=lambda r: -r["amount_at_stake"])
    for i, r in enumerate(rows, 1):
        r["pair_id"] = i
        r["amount_at_stake"] = f"{r['amount_at_stake']:.2f}"

    with open(os.path.join(args.out_dir, "awardee_match_review.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    jv = sum(1 for raw, *_ in names if is_joint_venture(raw))
    methods = defaultdict(int)
    for r in rows:
        methods[r["match_method"]] += 1
    print(f"Awardee names:            {len(names):,} ({jv:,} joint ventures left out)")
    print(f"After auto-merge:         {len(groups):,} ({merged:,} groups of spellings merged)")
    print(f"Pairs to review:          {len(rows):,} " + ", ".join(f"{m}: {n:,}" for m, n in sorted(methods.items())))
    suggested = defaultdict(int)
    for r in rows:
        suggested[r["suggested_decision"] or "none"] += 1
    print(f"Suggested decisions:      " + ", ".join(f"{s}: {n:,}" for s, n in sorted(suggested.items())))
    labels = defaultdict(int)
    for r in rows:
        labels[r["relation_label"]] += 1
    print(f"Triage labels:            " + ", ".join(f"{s}: {n:,}" for s, n in sorted(labels.items())))
    print(f"Written to {args.out_dir}/auto_merges.csv and {args.out_dir}/awardee_match_review.csv")


if __name__ == "__main__":
    main()
