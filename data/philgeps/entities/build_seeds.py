"""
Build contractor entity seeds from match_awardees.py output and a reviewed pair file.

Reads:
    auto_merges.csv            spellings merged automatically
    awardee_match_review.csv   pairs with `decision` filled in (yes / no / unsure);
                               only "yes" pairs are merged
Writes:
    entity.csv        entity_id, canonical_name, entity_type, uacs_code
    entity_alias.csv  raw_name, source, entity_id, match_method, confidence, reviewed

Only names that belong to a multi-name entity are listed. Any awardee_name not in
entity_alias.csv is its own entity. Merges are transitive: if A = B and B = C are
accepted, A, B and C become one entity. The canonical name is the spelling with
the largest contract amount, and entity_id is derived from it.

Usage (from data/philgeps/entities):
    python build_seeds.py --matches out --review out/awardee_match_review.csv --out-dir out
"""

import argparse
import csv
import hashlib
import os
from collections import defaultdict


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


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--matches", default="out", help="Directory with auto_merges.csv")
    parser.add_argument("--review", default="out/awardee_match_review.csv", help="Reviewed pair file")
    parser.add_argument("--out-dir", default="out", help="Output directory")
    args = parser.parse_args()

    uf = UnionFind()
    amount = defaultdict(float)
    method = {}

    with open(os.path.join(args.matches, "auto_merges.csv"), newline="", encoding="utf-8") as f:
        by_key = defaultdict(list)
        for row in csv.DictReader(f):
            by_key[row["clean_key"]].append(row["awardee_name"])
            amount[row["awardee_name"]] = float(row["amount"])
            method[row["awardee_name"]] = "normalized"
        for names in by_key.values():
            for name in names[1:]:
                uf.union(name, names[0])

    accepted = rejected = undecided = 0
    with open(args.review, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            decision = row["decision"].strip().lower()
            if decision in ("yes", "y"):
                accepted += 1
                for side in ("a", "b"):
                    name = row[f"name_{side}"]
                    amount.setdefault(name, float(row[f"amount_{side}"]))
                uf.union(row["name_a"], row["name_b"])
                method[row["name_a"]] = method[row["name_b"]] = "reviewed"
            elif decision in ("no", "n"):
                rejected += 1
            else:
                undecided += 1

    members = defaultdict(list)
    for name in list(uf.parent):
        members[uf.find(name)].append(name)

    entities, aliases = [], []
    for names in members.values():
        if len(names) < 2:
            continue
        canonical = max(names, key=lambda n: (amount.get(n, 0.0), n))
        entity_id = "ctr-" + hashlib.md5(canonical.encode("utf-8")).hexdigest()[:16]
        reviewed = any(method.get(n) == "reviewed" for n in names)
        entities.append([entity_id, canonical, "contractor", ""])
        for name in sorted(names):
            aliases.append([name, "philgeps", entity_id, method.get(name, "reviewed"),
                            "1.0", "true" if reviewed else "false"])

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "entity.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "canonical_name", "entity_type", "uacs_code"])
        w.writerows(sorted(entities))
    with open(os.path.join(args.out_dir, "entity_alias.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_name", "source", "entity_id", "match_method", "confidence", "reviewed"])
        w.writerows(sorted(aliases))

    print(f"Review pairs: {accepted} accepted, {rejected} rejected, {undecided} undecided")
    print(f"Entities with more than one name: {len(entities):,} covering {len(aliases):,} names")


if __name__ == "__main__":
    main()
