"""
Keep reviewer decisions about awardee name pairs in one editable file.

The decisions file (decisions/awardee_decisions.csv) is the only place where people
record judgments. Pipeline outputs are regenerated from it and never edit a decision.
Pairs are keyed by the two names, not by pair_id, so decisions survive reruns.

Columns
    name_a, name_b     the two awardee names, exactly as in PhilGEPS
    decision           merge     same registered business (incl. a documented rename)
                       separate  different businesses
                       related   different registrations of the same owner or group;
                                 linked, totals not combined
                       branch    branches or units of parent_name; linked, totals rolled
                                 up under the parent
                       pending   not decided yet; treated as separate
    parent_name        for branch: the parent's name (may be one of the two names)
    status             provisional  set from automated evidence, not checked by a person
                       confirmed    checked by a person
    basis              what the decision rests on, e.g. "same DPWH contractor ID",
                       "SEC registration CS2019-01234"
    notes, reviewer, reviewed_on
    assigned_to        who a pair is handed to (set by `export --assign`); cleared by `apply`
    amount_at_stake, evidence   context from the latest match run (refreshed by `import`;
                                editing them has no effect)

Commands (from data/philgeps/entities):
    python review_decisions.py import --from out/awardee_match_review.csv
        Add pairs that aren't in the decisions file yet and refresh context columns.
        A filled `decision` column (yes/no/unsure) becomes a confirmed decision; if there
        is a `next_action` column, it becomes a provisional one (see NEXT_ACTION).
        Existing decisions are never changed.
    python review_decisions.py check
        Validate the file and report conflicts (e.g. A=B and B=C merged, A-C separate).
    python review_decisions.py export --decision pending --assign "registry reviewer" \
            --review out/awardee_match_review.csv --out pending_lookups.csv
        Write a self-contained worksheet for another reviewer (all evidence plus blank
        answer columns) and mark the pairs as assigned. The decisions file is not
        handed out.
    python review_decisions.py apply --from pending_lookups.csv
        Bring a returned worksheet back: rows with an answer update pairs that are
        still pending; anything else is reported and left alone.
"""

import argparse
import csv
import html
import os
import re
import sys
from collections import defaultdict

DECISIONS = os.path.join("decisions", "awardee_decisions.csv")
COLUMNS = ["name_a", "name_b", "decision", "parent_name", "status", "basis", "notes",
           "reviewer", "reviewed_on", "assigned_to", "amount_at_stake", "evidence"]
EDITABLE = ["decision", "parent_name", "status", "basis", "notes", "reviewer", "reviewed_on", "assigned_to"]

# Worksheet for an outside reviewer: context first, then the answer columns.
WORKSHEET_CONTEXT = [
    "priority", "name_a", "name_b", "amount_at_stake", "why_pending",
    "other_spellings_a", "other_spellings_b", "rows_a", "amount_a", "rows_b", "amount_b",
    "years_a", "years_b", "shared_organizations", "organizations_a", "organizations_b",
    "top_shared_organizations", "notes_a", "notes_b", "dpwh_contractor_a", "dpwh_contractor_b",
    "relation_label", "relation_evidence",
]
WORKSHEET_ANSWERS = [
    "answer", "parent_name", "registry_a", "registration_no_a", "registry_b", "registration_no_b",
    "source_notes", "reviewer", "reviewed_on",
]
VALID_DECISIONS = {"merge", "separate", "related", "branch", "pending"}
VALID_STATUS = {"provisional", "confirmed"}

# Amount at stake from which "merge; confirm ... if amount is material" waits for a lookup.
MATERIAL_AMOUNT = 1e9

# next_action text -> decision. "merge_if_immaterial" is resolved per pair below.
NEXT_ACTION = {
    "merge": "merge",
    "merge; confirm with SEC/DTI lookup if amount is material": "merge_if_immaterial",
    "keep separate": "separate",
    "keep separate; pull GIS/DTI owner before linking": "separate",
    "keep separate; link as related; pull GIS for shared officers": "related",
    "keep units separate; roll up to parent for totals": "branch",
    "registry lookup on both names": "pending",
}

REVIEW_DECISION = {"yes": "merge", "y": "merge", "no": "separate", "n": "separate", "unsure": "pending"}


def pair_key(a, b):
    return tuple(sorted((a, b)))


def read_decisions(path=DECISIONS):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{c: row.get(c, "") or "" for c in COLUMNS} for row in csv.DictReader(f)]


def write_decisions(rows, path=DECISIONS):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = sorted(rows, key=lambda r: (-float(r["amount_at_stake"] or 0), r["name_a"], r["name_b"]))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def parent_of(a, b):
    """Shared leading part of two branch names, e.g. "UNIVERSITY OF THE PHILIPPINES"
    for "... - MANILA" and "... - DILIMAN". Returns "" if there isn't a usable one."""
    da, db = html.unescape(a.replace("&AMP;", "&")), html.unescape(b.replace("&AMP;", "&"))
    n = 0
    while n < min(len(da), len(db)) and da[n] == db[n]:
        n += 1
    prefix = da[:n]
    if n < min(len(da), len(db)) and da[n].isalnum() and db[n].isalnum():
        prefix = re.sub(r"[\w.]*$", "", prefix)  # don't end mid-word
    prefix = prefix.rstrip(" -,/(")
    if len(prefix) < 5:
        return ""
    for name, decoded in ((a, da), (b, db)):
        if decoded.rstrip(" -,") == prefix:
            return name
    return prefix


def evidence_text(row):
    parts = [row.get("match_method", ""), row.get("dpwh_evidence", ""),
             f"{row.get('shared_organizations', '')} shared buyers"]
    if row.get("relation_evidence"):
        parts.append(row["relation_evidence"])
    return "; ".join(p for p in parts if p and p != " shared buyers")


def from_review_row(row):
    """Decision fields for a pair that isn't in the decisions file yet."""
    stake = float(row.get("amount_at_stake") or 0)
    filled = (row.get("decision") or "").strip().lower()
    if filled in REVIEW_DECISION:
        return {"decision": REVIEW_DECISION[filled], "status": "confirmed", "basis": "review file decision",
                "notes": row.get("reviewer_notes", "")}
    action = (row.get("next_action") or "").strip()
    if action not in NEXT_ACTION:
        return {"decision": "pending", "status": "provisional", "basis": "", "notes": ""}
    decision, notes = NEXT_ACTION[action], ""
    if decision == "merge_if_immaterial":
        if row.get("dpwh_evidence") in ("mixed_ids", "different_id"):
            decision, notes = "pending", "DPWH links these names to more than one contractor ID; registry lookup needed"
        elif stake >= MATERIAL_AMOUNT:
            decision, notes = "pending", f"amount at stake >= {MATERIAL_AMOUNT:,.0f}; confirm with SEC/DTI lookup before merging"
        else:
            decision = "merge"
    if action == "keep separate; pull GIS/DTI owner before linking":
        notes = "possible related party; check GIS/DTI owner before linking"
    if action == "keep separate; link as related; pull GIS for shared officers":
        notes = "verify shared officers (GIS) before showing as related"
    try:
        similar = float(row.get("core_similarity") or 0) >= 0.9 and int(row.get("shared_organizations") or 0) >= 3
    except ValueError:
        similar = False
    if decision == "separate" and similar:
        notes = (notes + "; " if notes else "") + "near-identical names with shared buyers: possibly a typo of one name"
    out = {"decision": decision, "status": "provisional", "basis": f"next_action: {action}", "notes": notes}
    if decision == "branch":
        out["parent_name"] = parent_of(row["name_a"], row["name_b"])
    return out


def cmd_import(args):
    existing = {pair_key(r["name_a"], r["name_b"]): r for r in read_decisions(args.decisions)}
    added = refreshed = 0
    with open(getattr(args, "from"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = pair_key(row["name_a"], row["name_b"])
            context = {"amount_at_stake": f"{float(row.get('amount_at_stake') or 0):.2f}",
                       "evidence": evidence_text(row)}
            if key in existing:
                # Don't replace triage detail (relation_evidence) with the plain match output.
                if not row.get("relation_evidence") and "relation_evidence" not in row and existing[key]["evidence"]:
                    context.pop("evidence")
                existing[key].update(context)
                refreshed += 1
                continue
            new = {c: "" for c in COLUMNS}
            new.update(name_a=row["name_a"], name_b=row["name_b"], reviewer=args.reviewer, **context)
            new.update(from_review_row(row))
            existing[key] = new
            added += 1
    rows = list(existing.values())
    write_decisions(rows, args.decisions)
    counts = defaultdict(int)
    for r in rows:
        counts[(r["decision"], r["status"])] += 1
    print(f"{added} pairs added, {refreshed} existing pairs kept (context refreshed); {len(rows)} in {args.decisions}")
    for (d, s), n in sorted(counts.items()):
        print(f"  {d:9} {s:12} {n}")


def validate(rows):
    """Return a list of problems: invalid values, duplicates, and merge conflicts."""
    problems, seen = [], set()
    for i, r in enumerate(rows, 2):
        where = f"line {i} ({r['name_a']} / {r['name_b']})"
        key = pair_key(r["name_a"], r["name_b"])
        if key in seen:
            problems.append(f"{where}: pair appears more than once")
        seen.add(key)
        if r["decision"] not in VALID_DECISIONS:
            problems.append(f"{where}: decision must be one of {sorted(VALID_DECISIONS)}, not {r['decision']!r}")
        if r["status"] not in VALID_STATUS:
            problems.append(f"{where}: status must be one of {sorted(VALID_STATUS)}, not {r['status']!r}")
        if r["decision"] == "branch" and not r["parent_name"].strip():
            problems.append(f"{where}: branch needs a parent_name")
    uf = UnionFind()
    for r in rows:
        if r["decision"] == "merge":
            uf.union(r["name_a"], r["name_b"])
    for r in rows:
        if r["decision"] in ("separate", "related", "branch") and uf.find(r["name_a"]) == uf.find(r["name_b"]):
            problems.append(f"conflict: {r['name_a']} / {r['name_b']} is marked {r['decision']}, "
                            f"but other merge decisions join them into one entity")
    return problems


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def cmd_check(args):
    rows = read_decisions(args.decisions)
    problems = validate(rows)
    for p in problems:
        print(p)
    print(f"{len(rows)} decisions, {len(problems)} problems")
    return 1 if problems else 0


def cmd_export(args):
    decisions = read_decisions(args.decisions)
    review = {}
    if args.review and os.path.exists(args.review):
        with open(args.review, newline="", encoding="utf-8") as f:
            review = {pair_key(r["name_a"], r["name_b"]): r for r in csv.DictReader(f)}
    chosen = [d for d in decisions if d["decision"] == args.decision]
    out = []
    for d in chosen:
        r = review.get(pair_key(d["name_a"], d["name_b"]), {})
        stake = float(d["amount_at_stake"] or 0)
        row = {c: r.get(c, "") for c in WORKSHEET_CONTEXT}
        row.update(priority="high" if stake >= MATERIAL_AMOUNT else "normal", name_a=d["name_a"], name_b=d["name_b"],
                   amount_at_stake=d["amount_at_stake"], why_pending=d["notes"] or d["basis"])
        row.update({c: "" for c in WORKSHEET_ANSWERS})
        out.append(row)
        if args.assign:
            d["assigned_to"] = args.assign
    out.sort(key=lambda r: (r["priority"] != "high", -float(r["amount_at_stake"] or 0)))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=WORKSHEET_CONTEXT + WORKSHEET_ANSWERS)
        w.writeheader()
        w.writerows(out)
    if args.assign:
        write_decisions(decisions, args.decisions)
    high = sum(1 for r in out if r["priority"] == "high")
    print(f"{len(out)} {args.decision} pairs written to {args.out} ({high} high priority)"
          + (f"; marked assigned_to = {args.assign!r}" if args.assign else ""))


ANSWERS = {"merge", "separate", "related", "branch", "unsure"}


def cmd_apply(args):
    decisions = read_decisions(args.decisions)
    by_key = {pair_key(d["name_a"], d["name_b"]): d for d in decisions}
    applied, skipped, problems = 0, 0, []
    with open(getattr(args, "from"), newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            answer = (row.get("answer") or "").strip().lower()
            if not answer:
                skipped += 1
                continue
            where = f"line {i} ({row['name_a']} / {row['name_b']})"
            d = by_key.get(pair_key(row["name_a"], row["name_b"]))
            if d is None:
                problems.append(f"{where}: pair not in the decisions file")
                continue
            if d["decision"] != "pending":
                problems.append(f"{where}: already decided as {d['decision']} ({d['status']}); not overwritten")
                continue
            if answer not in ANSWERS:
                problems.append(f"{where}: answer must be one of {sorted(ANSWERS)}, not {answer!r}")
                continue
            if answer == "branch" and not (row.get("parent_name") or "").strip():
                problems.append(f"{where}: branch needs a parent_name")
                continue
            if answer == "unsure":
                d["notes"] = "; ".join(x for x in (d["notes"], f"outside review unsure: {row.get('source_notes', '')}") if x)
                d["assigned_to"] = ""
                applied += 1
                continue
            regs = [f"{row.get(f'registry_{s}', '').strip()} {row.get(f'registration_no_{s}', '').strip()}".strip()
                    for s in ("a", "b")]
            basis = "; ".join(f"{n}: {r}" for n, r in zip((row["name_a"], row["name_b"]), regs) if r)
            confirmed = bool(basis and (row.get("reviewer") or "").strip())
            d.update(decision=answer, parent_name=(row.get("parent_name") or "").strip(),
                     status="confirmed" if confirmed else "provisional",
                     basis=basis or "outside review, no registration recorded",
                     notes="; ".join(x for x in (d["notes"], row.get("source_notes", "").strip()) if x),
                     reviewer=(row.get("reviewer") or "").strip(), reviewed_on=(row.get("reviewed_on") or "").strip(),
                     assigned_to="")
            if not confirmed:
                problems.append(f"{where}: applied as provisional (needs both a registration and a reviewer name to be confirmed)")
            applied += 1
    conflicts = validate(decisions)
    if conflicts:
        for p in problems + conflicts:
            print(p)
        print(f"\nThe worksheet would make the decisions file invalid ({len(conflicts)} problems). Nothing written.")
        return 1
    write_decisions(decisions, args.decisions)
    for p in problems:
        print(p)
    print(f"{applied} answers applied, {skipped} rows without an answer left pending, {len(problems)} notes above")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--decisions", default=DECISIONS, help="Decisions file")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import", help="Add new pairs from a review file")
    p.add_argument("--from", required=True, help="Review CSV (match_awardees.py output, optionally with next_action)")
    p.add_argument("--reviewer", default="", help="Name to record on newly added decisions")
    sub.add_parser("check", help="Validate the decisions file")
    p = sub.add_parser("export", help="Write a worksheet for another reviewer")
    p.add_argument("--decision", default="pending", help="Which decisions to export")
    p.add_argument("--review", default="out/awardee_match_review.csv", help="Match output, for evidence columns")
    p.add_argument("--out", required=True, help="Worksheet to write")
    p.add_argument("--assign", default="", help="Mark exported pairs as assigned to this reviewer")
    p = sub.add_parser("apply", help="Apply a returned worksheet")
    p.add_argument("--from", required=True, help="Worksheet with answers filled in")
    args = parser.parse_args()
    commands = {"import": cmd_import, "check": cmd_check, "export": cmd_export, "apply": cmd_apply}
    sys.exit(commands[args.command](args))


if __name__ == "__main__":
    main()
