"""
Split joint-venture awardee names into their member contractors.

A joint venture (JV) stays its own awardee: it signed the contract. Membership is
recorded alongside, so member contractors can be credited for JV work.

Commands (from data/philgeps/entities, after match_awardees.py and build_seeds.py):
    python jv_members.py propose --input ../philgeps.parquet --dpwh dpwh_transparency_data.parquet
        Writes out/jv_review.csv: each JV name, its proposed members and the evidence.
    python jv_members.py import --from out/jv_review.csv
        Adds JV names that aren't in decisions/jv_decisions.csv yet, with the proposed
        members as a provisional decision. Existing decisions are never changed.

Which names are JVs
    - names with an explicit marker (JOINT VENTURE, (J.V.), - JV, CONSORTIUM, ...), and
    - names with a "/" between two parts that are each a known contractor (an awardee
      in their own right), e.g. THREE W BUILDERS, INC./TAGUM BUILDERS CONTRACTORS CORP.
      A "/" alone isn't enough: "PERSONS W/ DISABILITY", "GLASS/ALUMINUM".

How members are found
    1. Markers and notes are removed ("JOINT VENTURE OF", "(AMSCO-JV)", "(FORMERLY: ...)").
    2. Candidate splits are tried at "/", " - ", ",", " AND ", " & " and " WITH ". The
       split whose parts all match known contractors wins; ties go to fewer, longer parts.
       A part matches when its clean_key equals a known awardee's (exact) or its core_key
       equals exactly one known awardee's (core).
    3. If no split matches fully, the split at "/" or " - " (else the best partial one) is
       proposed with its unmatched parts flagged for review.
    4. DPWH evidence: where the JV has DPWH contracts, DPWH's contractor field lists the
       members as "NAME (ID) / NAME (ID)". These are compared with the proposed split.
"""

import argparse
import bisect
import csv
import os
import re
import sys
from collections import defaultdict

from difflib import SequenceMatcher

import duckdb

from match_awardees import distinctive
from normalize import decode_entities, is_joint_venture, name_keys, split_annotations

JV_DECISIONS = os.path.join("decisions", "jv_decisions.csv")
DECISION_COLUMNS = ["jv_name", "members", "status", "basis", "notes", "reviewer", "reviewed_on",
                    "assigned_to", "amount", "evidence"]
REVIEW_COLUMNS = ["jv_name", "rows", "amount", "detected_by", "proposed_members", "member_match",
                  "dpwh_members", "dpwh_agrees", "needs_review", "review_reason", "next_action"]

# Markers between two members become a separator; others are removed.
MID_MARKERS = [r"\bJOINT? VENTURE (WITH|AND)\b", r"\bJV WITH\b", r"\bIN JOINT? VENTURE WITH\b"]
MARKERS = [
    r"^\s*(A\s+)?JOINT? VENTURE (OF|BETWEEN)\b", r"\bJOINT? VENTURE\b", r"\bCONSORTIUM\b",
    r"\(\s*[A-Z0-9 -]*\bJV\b[A-Z0-9 -]*\)", r"\(\s*J\.?\s?V\.?\s*\)", r"\bJ\.?\s?V\.?\s*$",
    r"[-/ ]JV\b", r"\bJV\b",
]
SEPARATORS = [r"\s*/\s*", r"\s+-\s+|\s*-\s+|\s+-\s*", r"\s*,\s*", r"\s+AND\s+", r"\s*&\s*", r"\s+WITH\s+"]
DPWH_CONTRACT_ID = r"^([0-9]{2}[A-Z]{1,2}[0-9]{4,5})\b"
DPWH_CONTRACTOR = re.compile(r"(.+?)\s*\((\d+)\)")
MEMBER_SEP = " | "


def strip_markers(name):
    name, _ = split_annotations(decode_entities(name).upper())
    for m in MID_MARKERS:
        name = re.sub(m, " / ", name)
    for m in MARKERS:
        name = re.sub(m, " ", name)
    return re.sub(r"\s+", " ", name).strip(" -/,&()")


class Contractors:
    """Known non-JV awardee names, looked up by clean_key and core_key."""

    def __init__(self, names):
        self.by_clean, self.by_core = {}, defaultdict(set)
        for n in names:
            k = name_keys(n)
            if k["clean_key"]:
                self.by_clean.setdefault(k["clean_key"], n)
                self.by_core[k["core_key"]].add(k["clean_key"])
        self.sorted_cores = sorted(self.by_core)

    def match(self, part):
        """(awardee name, how) for a name part, or (None, "unmatched")."""
        k = name_keys(part)
        if len(k["clean_key"]) < 3:
            return None, "unmatched"
        if k["clean_key"] in self.by_clean:
            return self.by_clean[k["clean_key"]], "exact"
        cores = self.by_core.get(k["core_key"], set())
        if len(cores) == 1:
            return self.by_clean[next(iter(cores))], "core"
        # A short form of a longer name (MEDILINES -> MEDILINES DISTRIBUTORS INC.), if unique.
        # Only for names without a legal form: NTT DATA CORPORATION is a full name of its own.
        if len(k["core_key"]) >= 6 and k["core_key"] == k["clean_key"]:
            prefix = k["core_key"] + " "
            lo = bisect.bisect_left(self.sorted_cores, prefix)
            hi = bisect.bisect_left(self.sorted_cores, prefix + "\uffff")
            longer = [c for core in self.sorted_cores[lo:hi] for c in self.by_core[core]]
            if len(longer) == 1:
                return self.by_clean[longer[0]], "prefix"
        return None, "unmatched"


LEGAL_FORM_START = re.compile(r"^(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|OPC)\b\.?")


def valid_parts(parts):
    """Parts must look like names: not a leftover legal form such as "INC. AND ..."."""
    return all(len(p) >= 3 and not LEGAL_FORM_START.match(p) for p in parts)


def candidate_splits(text):
    """All ways to split text into 2+ parts at one kind of separator, and at "/" then
    one more separator inside each side (for "A AND B / C")."""
    out = []
    for sep in SEPARATORS:
        parts = [p.strip(" -/,&()") for p in re.split(sep, text) if p.strip(" -/,&()")]
        if len(parts) >= 2:
            out.append(parts)
            # also every 2-way split at this separator, for names with the separator inside
            pieces = re.split(f"({sep})", text)
            for i in range(1, len(pieces) - 1, 2):
                a, b = "".join(pieces[:i]).strip(" -/,&()"), "".join(pieces[i + 1:]).strip(" -/,&()")
                if a and b:
                    out.append([a, b])
    return out


def best_split(text, contractors, depth=0):
    best = None
    for parts in candidate_splits(text):
        if not valid_parts(parts):
            continue
        matches, seen = [], set()
        for p in parts:
            m = contractors.match(p)
            if m[0] in seen:  # two members can't be the same contractor
                m = (None, "unmatched")
            seen.add(m[0])
            matches.append(m)
        matched = sum(1 for m, _ in matches if m)
        score = (matched == len(parts), matched, -len(parts))
        if best is None or score > best[0]:
            best = (score, parts, matches)
    if best is None or best[0][0] or depth > 1:
        return best
    # Split unmatched parts again: "A / B & C" where B and C are separate members.
    parts, matches = [], []
    for p, m in zip(best[1], best[2]):
        sub = best_split(p, contractors, depth + 1) if not m[0] else None
        if sub and (sub[0][1] > 0 or "/" in p):
            parts += sub[1]
            matches += sub[2]
        else:
            parts.append(p)
            matches.append(m)
    matched = sum(1 for m, _ in matches if m)
    return ((matched == len(parts), matched, -len(parts)), parts, matches)


DPWH_NOTE = re.compile(r"\(\s*(FORMERLY|FOR)\b\s*:?\s*(.*?)\)?\s*$", re.I)


def dpwh_name(name):
    """DPWH contractor name without "(FORMERLY: ...)" / "(FOR: ...)", which may be cut off."""
    return DPWH_NOTE.sub("", name).strip()


def dpwh_former(name):
    """The former name in a DPWH "(FORMERLY: ...)" note, or ""."""
    m = DPWH_NOTE.search(name)
    return m.group(2).strip() if m else ""


def same_member(a, b):
    """Whether two spellings name the same member, comparing distinctive words only:
    ALLENCON = ALLENCON DEVELOPMENT CORPORATION, ME3 = ME 3."""
    da = distinctive(name_keys(a)["core_key"]).replace(" ", "")
    db = distinctive(name_keys(b)["core_key"]).replace(" ", "")
    if not da or not db:
        return False
    short, long_ = sorted((da, db), key=len)
    return SequenceMatcher(None, da, db).ratio() >= 0.85 or (len(short) >= 4 and long_.startswith(short))


def pair_with_dpwh(members, listed):
    """Match our members to DPWH's list. Returns {member index: DPWH name} or None if
    the lists don't correspond one to one."""
    if len(members) != len(listed):
        return None
    pairing, used = {}, set()
    for i, m in enumerate(members):
        for j, (n, _) in enumerate(listed):
            if j not in used and (same_member(m, dpwh_name(n)) or (dpwh_former(n) and same_member(m, dpwh_former(n)))):
                pairing[i] = dpwh_name(n)
                used.add(j)
                break
    return pairing if len(pairing) == len(members) else None


def propose(args):
    con = duckdb.connect()
    stats = con.execute(f"""
        SELECT awardee_name, count(*), sum(contract_amount)
        FROM read_parquet('{args.input}') WHERE awardee_name IS NOT NULL GROUP BY 1
        ORDER BY 3 DESC, 1
    """).fetchall()  # largest first, so each contractor is named by its largest spelling
    marker_jv = {n for n, _, _ in stats if is_joint_venture(n)}
    contractors = Contractors([n for n, _, _ in stats if n not in marker_jv and "/" not in n])

    dpwh = defaultdict(set)
    if args.dpwh:
        for name, contractor in con.execute(f"""
            SELECT DISTINCT p.awardee_name, d.contractor
            FROM (SELECT awardee_name,
                         coalesce(nullif(regexp_extract(award_title, '{DPWH_CONTRACT_ID}', 1), ''),
                                  nullif(regexp_extract(notice_title, '{DPWH_CONTRACT_ID}', 1), '')) AS contract_id
                  FROM read_parquet('{args.input}')) p
            JOIN read_parquet('{args.dpwh}') d ON d.contractId = p.contract_id
        """).fetchall():
            entries = [(m.group(1).strip(" /"), m.group(2)) for m in DPWH_CONTRACTOR.finditer(contractor or "")]
            if len(entries) >= 2:
                dpwh[name].add(tuple(sorted(entries, key=lambda e: e[1])))

    rows = []
    for name, n_rows, amount in stats:
        is_marker = name in marker_jv
        if not is_marker and "/" not in name:
            continue
        text = strip_markers(name)
        split = best_split(text, contractors)
        if split is None:
            if is_marker:
                rows.append(dict(jv_name=name, rows=n_rows, amount=amount, detected_by="marker",
                                 proposed_members="", member_match="", needs_review="yes",
                                 review_reason="could not split into members"))
            continue
        (full, matched, _), parts, matches = split
        if not is_marker and not full:
            continue  # a "/" in an ordinary name
        members = [m or p for p, (m, _) in zip(parts, matches)]
        how = [h for _, h in matches]
        dpwh_lists = dpwh.get(name, set())
        dpwh_text, agrees = "", ""
        if dpwh_lists:
            # DPWH can list different members on different contracts of one JV name;
            # use a listing that matches the split if there is one.
            listings = sorted(dpwh_lists)
            listed, pairing = listings[0], None
            for candidate in listings:
                pairing = pair_with_dpwh(members, candidate)
                if pairing is not None:
                    listed = candidate
                    break
            dpwh_text = MEMBER_SEP.join(f"{n} ({i})" for n, i in listed)
            if len(listings) > 1:
                dpwh_text += f" [{len(listings)} different DPWH listings]"
            agrees = "yes" if pairing is not None else "no"
            # Members we couldn't find as contractors: try DPWH's fuller spelling.
            for i, dn in (pairing or {}).items():
                if how[i] == "unmatched":
                    found, h = contractors.match(dn)
                    members[i], how[i] = (found, "dpwh") if found else (dn, "dpwh_name")
            full = all(h != "unmatched" for h in how)
            matched = sum(h != "unmatched" for h in how)
        reasons = []
        if not full:
            reasons.append(f"{len(how) - matched} member(s) not found as a contractor")
        if "prefix" in how:
            reasons.append("a member was matched as a short form of a longer name")
        if agrees == "no":
            reasons.append("DPWH lists different members")
        if not is_marker:
            reasons.append("no JV marker; detected because both sides are known contractors")
        rows.append(dict(
            jv_name=name, rows=n_rows, amount=amount, detected_by="marker" if is_marker else "slash",
            proposed_members=MEMBER_SEP.join(members),
            member_match=MEMBER_SEP.join(how),
            dpwh_members=dpwh_text, dpwh_agrees=agrees,
            needs_review="yes" if reasons and agrees != "yes" else "no",
            review_reason="; ".join(reasons),
        ))
    for r in rows:
        r["next_action"] = "check members" if r["needs_review"] == "yes" else "accept members"
        r["amount"] = f"{r['amount']:.2f}"
    rows.sort(key=lambda r: -float(r["amount"]))
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, "jv_review.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    review = [r for r in rows if r["needs_review"] == "yes"]
    print(f"JV names: {len(rows):,} ({sum(r['detected_by'] == 'slash' for r in rows)} found by '/' alone), "
          f"PHP {sum(float(r['amount']) for r in rows) / 1e9:,.1f}B")
    print(f"  all members matched: {sum(1 for r in rows if r['member_match'] and 'unmatched' not in r['member_match']):,}; "
          f"DPWH agrees: {sum(r.get('dpwh_agrees') == 'yes' for r in rows):,}; needs review: {len(review):,} "
          f"(PHP {sum(float(r['amount']) for r in review) / 1e9:,.1f}B)")
    print(f"Written to {path}")


def read_jv_decisions(path=JV_DECISIONS):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{c: row.get(c, "") or "" for c in DECISION_COLUMNS} for row in csv.DictReader(f)]


def write_jv_decisions(rows, path=JV_DECISIONS):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = sorted(rows, key=lambda r: (-float(r["amount"] or 0), r["jv_name"]))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DECISION_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def cmd_import(args):
    existing = {r["jv_name"]: r for r in read_jv_decisions(args.decisions)}
    added = 0
    with open(getattr(args, "from"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            evidence = "; ".join(x for x in (
                f"split: {row['member_match']}" if row["member_match"] else "",
                f"DPWH: {row['dpwh_members']} (agrees: {row['dpwh_agrees']})" if row["dpwh_members"] else "",
                row["review_reason"]) if x)
            if row["jv_name"] in existing:
                existing[row["jv_name"]].update(amount=row["amount"], evidence=evidence)
                continue
            existing[row["jv_name"]] = {
                "jv_name": row["jv_name"], "members": row["proposed_members"], "status": "provisional",
                "basis": "DPWH contractor listing" if row["dpwh_agrees"] == "yes" else "name split",
                "notes": row["review_reason"], "reviewer": "", "reviewed_on": "", "assigned_to": "",
                "amount": row["amount"], "evidence": evidence,
            }
            added += 1
    write_jv_decisions(list(existing.values()), args.decisions)
    print(f"{added} JV names added; {len(existing)} in {args.decisions}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--decisions", default=JV_DECISIONS, help="JV decisions file")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("propose", help="Propose members for each JV name")
    p.add_argument("--input", default="../philgeps.parquet")
    p.add_argument("--dpwh", help="dpwh_transparency_data.parquet, for DPWH member listings")
    p.add_argument("--out-dir", default="out")
    p = sub.add_parser("import", help="Add new JV names to the decisions file")
    p.add_argument("--from", required=True)
    args = parser.parse_args()
    sys.exit(propose(args) if args.command == "propose" else cmd_import(args))


if __name__ == "__main__":
    main()
