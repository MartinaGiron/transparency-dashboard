"""
Build contractor entity seeds from automatic merges and the reviewer decisions file.

Reads:
    out/auto_merges.csv                  spellings merged automatically (match_awardees.py)
    decisions/awardee_decisions.csv      reviewer decisions (review_decisions.py)
    ../philgeps.parquet                  for amounts (choosing canonical names, summary)
Writes to --out-dir:
    entity.csv           entity_id, canonical_name, entity_type, uacs_code
    entity_alias.csv     raw_name, source, entity_id, match_method, confidence, reviewed
    entity_relation.csv  entity_id, related_entity_id, relation, status, basis
    entity_summary.csv   largest entities: total vs. their largest single spelling
    entity_membership.csv          parent_entity_id (JV), member_entity_id, share, evidence
    contractor_jv_summary.csv      per contractor: direct total, JV "involved in" total
                                   (full JV amount) and "attributed" total (JV amount x share)

Rules:
    - merge decisions (and automatic merges) join names into one entity; merges are
      transitive.
    - related -> relation "related" between two entities; totals are not combined.
    - branch  -> each branch keeps its own entity, with relation "branch_of" to the
      entity for parent_name (created if parent_name isn't an awardee name).
    - separate and pending -> nothing.
    - match_method is normalized (automatic), evidence (provisional decision) or reviewed
      (confirmed decision); reviewed is true only for confirmed decisions.
    - Joint ventures (decisions/jv_decisions.csv, from jv_members.py): JV spellings
      with the same members are one JV entity; each member gets share 1/n.
    - Stops without writing if decisions conflict (see review_decisions.py check).

Only names in a multi-name entity or a relation are listed; any other awardee_name is
its own entity. The canonical name is the spelling with the largest contract amount,
with HTML entities decoded (&AMP; -> &); raw names are kept exactly as in PhilGEPS.
entity_id is derived from it, so it changes if a different spelling becomes the largest.

Usage (from data/philgeps/entities):
    python build_seeds.py --input ../philgeps.parquet
"""

import argparse
import csv
import hashlib
import os
import sys
from collections import defaultdict

import duckdb

from normalize import decode_entities
from jv_members import JV_DECISIONS, MEMBER_SEP, read_jv_decisions
from review_decisions import DECISIONS, UnionFind, read_decisions, validate


def entity_id(canonical):
    return "ctr-" + hashlib.md5(canonical.encode("utf-8")).hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", default="../philgeps.parquet", help="PhilGEPS parquet file")
    parser.add_argument("--matches", default="out", help="Directory with auto_merges.csv")
    parser.add_argument("--decisions", default=DECISIONS, help="Reviewer decisions file")
    parser.add_argument("--jv-decisions", default=JV_DECISIONS, help="Joint-venture decisions file")
    parser.add_argument("--out-dir", default="out", help="Output directory")
    parser.add_argument("--summary-size", type=int, default=50, help="Entities in entity_summary.csv")
    args = parser.parse_args()

    decisions = read_decisions(args.decisions)
    problems = validate(decisions)
    if problems:
        for p in problems:
            print(p)
        print(f"\n{len(problems)} problems in {args.decisions}; fix them and rerun. Nothing written.")
        sys.exit(1)

    amount = dict(duckdb.connect().execute(
        f"SELECT awardee_name, sum(contract_amount) FROM read_parquet('{args.input}') GROUP BY 1").fetchall())

    uf = UnionFind()
    method = {}
    with open(os.path.join(args.matches, "auto_merges.csv"), newline="", encoding="utf-8") as f:
        by_key = defaultdict(list)
        for row in csv.DictReader(f):
            by_key[row["clean_key"]].append(row["awardee_name"])
        for names in by_key.values():
            for name in names:
                uf.union(name, names[0])
                method[name] = "normalized"

    rank = {"normalized": 0, "evidence": 1, "reviewed": 2}
    for d in decisions:
        if d["decision"] != "merge":
            continue
        uf.union(d["name_a"], d["name_b"])
        m = "reviewed" if d["status"] == "confirmed" else "evidence"
        for name in (d["name_a"], d["name_b"]):
            if rank[m] > rank.get(method.get(name, "normalized"), 0) or name not in method:
                method[name] = m

    members = defaultdict(set)
    for name in list(uf.parent):
        members[uf.find(name)].add(name)

    def entity_of(name):
        """(entity_id, canonical name, member names) for any name, merged or not."""
        names = members.get(uf.find(name), {name}) if name in uf.parent else {name}
        canonical = decode_entities(max(names, key=lambda n: (amount.get(n, 0.0), n))).replace("\xa0", " ")
        return entity_id(canonical), canonical, names

    entities, aliases = {}, {}

    def add_entity(name):
        eid, canonical, names = entity_of(name)
        if eid not in entities:
            reviewed = any(method.get(n) == "reviewed" for n in names)
            entities[eid] = [eid, canonical, "contractor", ""]
            for n in names:
                aliases[n] = [n, "philgeps", eid, method.get(n, "evidence" if len(names) > 1 else "self"),
                              "1.0", "true" if reviewed else "false"]
        return eid

    for root, names in members.items():
        if len(names) > 1:
            add_entity(next(iter(names)))

    relations = []
    for d in decisions:
        if d["decision"] == "related":
            a, b = add_entity(d["name_a"]), add_entity(d["name_b"])
            relations.append([*sorted((a, b)), "related", d["status"], d["basis"]])
        elif d["decision"] == "branch":
            parent = d["parent_name"].strip()
            if parent in amount or parent in uf.parent:
                pid = add_entity(parent)
            else:
                parent = decode_entities(parent)
                pid = entity_id(parent)
                entities.setdefault(pid, [pid, parent, "contractor", ""])
            for child in (d["name_a"], d["name_b"]):
                cid = add_entity(child)
                if cid != pid:
                    relations.append([cid, pid, "branch_of", d["status"], d["basis"]])

    # Joint ventures: group spellings by their set of member entities.
    jv_groups = defaultdict(list)
    for d in read_jv_decisions(args.jv_decisions):
        names = [m.strip() for m in d["members"].split(MEMBER_SEP) if m.strip()]
        member_ids = []
        for m in names:
            if m in amount or m in uf.parent:
                mid = add_entity(m)
            else:  # a partner that never won a contract on its own, e.g. a foreign firm
                canonical = decode_entities(m)
                mid = entity_id(canonical)
                entities.setdefault(mid, [mid, canonical, "contractor", ""])
            if mid not in member_ids:
                member_ids.append(mid)
        if len(member_ids) >= 2:
            jv_groups[tuple(sorted(member_ids))].append(d)
    memberships, jv_total = [], {}
    for member_ids, spellings in jv_groups.items():
        canonical = decode_entities(max((d["jv_name"] for d in spellings), key=lambda n: (amount.get(n, 0.0), n)))
        jid = "jv-" + hashlib.md5("|".join(member_ids).encode("utf-8")).hexdigest()[:16]
        entities[jid] = [jid, canonical, "joint_venture", ""]
        confirmed = all(d["status"] == "confirmed" for d in spellings)
        for d in spellings:
            aliases[d["jv_name"]] = [d["jv_name"], "philgeps", jid, "jv_members", "1.0", "true" if confirmed else "false"]
        jv_total[jid] = sum(amount.get(d["jv_name"], 0.0) for d in spellings)
        basis = "; ".join(sorted({d["basis"] for d in spellings}))
        for mid in member_ids:
            memberships.append([jid, mid, f"{1 / len(member_ids):.4f}",
                                f"{'confirmed' if confirmed else 'provisional'}: {basis}"])

    # Spellings now treated as a JV leave their contractor entity; drop entities left empty.
    used = {a[2] for a in aliases.values()} | {i for r in relations for i in r[:2]} | {i for r in memberships for i in r[:2]}
    entities = {k: v for k, v in entities.items() if k in used}

    os.makedirs(args.out_dir, exist_ok=True)

    def write(name, header, rows):
        with open(os.path.join(args.out_dir, name), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    write("entity.csv", ["entity_id", "canonical_name", "entity_type", "uacs_code"], sorted(entities.values()))
    write("entity_alias.csv", ["raw_name", "source", "entity_id", "match_method", "confidence", "reviewed"],
          sorted(aliases.values()))
    write("entity_relation.csv", ["entity_id", "related_entity_id", "relation", "status", "basis"],
          sorted({tuple(r) for r in relations}))

    summary = []
    for eid, (_, canonical, _, _) in entities.items():
        names = [n for n, a in aliases.items() if a[2] == eid]
        if len(names) < 2:
            continue
        total = sum(amount.get(n, 0.0) for n in names)
        largest = max(amount.get(n, 0.0) for n in names)
        summary.append([eid, canonical, len(names), f"{total:.2f}", f"{largest:.2f}", f"{total - largest:.2f}"])
    summary.sort(key=lambda r: -float(r[3]))
    write("entity_summary.csv", ["entity_id", "canonical_name", "spellings", "entity_total",
                                 "largest_spelling_total", "added_by_grouping"], summary[:args.summary_size])

    write("entity_membership.csv", ["parent_entity_id", "member_entity_id", "share", "evidence"], sorted(memberships))

    direct = defaultdict(float)
    for name, a in aliases.items():
        direct[a[2]] += amount.get(name, 0.0)
    for name, total in amount.items():
        if name not in aliases:
            direct[entity_id(decode_entities(name).replace("\xa0", " "))] += total
    involved, attributed, n_jv = defaultdict(float), defaultdict(float), defaultdict(int)
    for jid, mid, share, _ in memberships:
        involved[mid] += jv_total[jid]
        attributed[mid] += jv_total[jid] * float(share)
        n_jv[mid] += 1
    jv_rows = [[mid, entities[mid][1], f"{direct.get(mid, 0.0):.2f}", n_jv[mid], f"{involved[mid]:.2f}",
                f"{attributed[mid]:.2f}", f"{direct.get(mid, 0.0) + attributed[mid]:.2f}"] for mid in involved]
    jv_rows.sort(key=lambda r: -float(r[4]))
    write("contractor_jv_summary.csv", ["entity_id", "canonical_name", "direct_total", "joint_ventures",
                                        "jv_involved_total", "jv_attributed_total", "direct_plus_attributed"], jv_rows)

    by_decision = defaultdict(int)
    for d in decisions:
        by_decision[(d["decision"], d["status"])] += 1
    print("Decisions: " + ", ".join(f"{d} ({s}): {n}" for (d, s), n in sorted(by_decision.items())))
    print(f"Entities with more than one name: {len(summary):,}; aliases listed: {len(aliases):,}; "
          f"relations: {len(set(map(tuple, relations)))}")
    print(f"Joint ventures: {len(jv_groups):,} JV entities from {sum(len(v) for v in jv_groups.values()):,} spellings; "
          f"{len(involved):,} contractors with JV work")
    print(f"Written to {args.out_dir}/: entity, entity_alias, entity_relation, entity_membership, "
          f"entity_summary, contractor_jv_summary (.csv)")


if __name__ == "__main__":
    main()
