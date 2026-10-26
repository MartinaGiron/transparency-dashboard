"""
Triage labels for candidate awardee pairs: a rule cascade over name, buyer, year and
DPWH evidence. Adds to each review row:

    name_core_a/b      the name without legal forms and generic trade words, no spaces
    core_similarity    difflib ratio of the two cores
    buyer_overlap      shared buyers / buyers of the side with fewer buyers
    year_handoff       one name's activity starts within 2 years of the other's ending
    evidence_score     0-10, for ranking pairs within a label only
    relation_label     first matching rule of the cascade (see label())
    relation_evidence  the signals behind the label, as text
    next_action        what a reviewer should do with the pair

Thresholds (0.85, 0.75, 0.30, 3 buyers, 2 years) were set by hand from about 40 pairs,
not fitted to labeled data. See METHODOLOGY.md for known weaknesses.
"""

import re
import unicodedata
from difflib import SequenceMatcher

LEGAL_FORMS = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "OPC", "LTD", "LIMITED",
               "PHIL", "PHILS", "PHILIPPINES"}
TRADE_WORDS = {"CONSTRUCTION", "SUPPLY", "SUPPLIES", "TRADING", "ENTERPRISE", "ENTERPRISES", "BUILDERS",
               "BUILDER", "SERVICES", "SERVICE", "MARKETING", "DEVELOPMENT", "AND", "GEN", "GENERAL",
               "MERCHANDISE", "SOLUTION", "SOLUTIONS",
               # Not in the original description ("and similar"); inferred from its output.
               "CONST", "CONTRACTOR", "CONTRACTORS", "ENGINEERING", "INDUSTRIAL", "EQUIPMENT", "EQUIPT",
               "RENTAL", "RENTALS", "LUMBER", "GLASS", "MERCH", "CONSTRUCT", "BRANCH", "DEV",
               "INDUSTRY", "INDUSTRIES", "EQIUPT"}
# Misspellings are caught by similarity: 0.85 against trade words of 8+ letters
# (CONTRUCTION, CONZTRUCT), 0.9 against shorter ones (SUPPY), so that real names
# survive (SERVICO is not SERVICE, 0.857).
LONG_TRADE_WORDS = [w for w in TRADE_WORDS if len(w) >= 8]
SHORT_TRADE_WORDS = [w for w in TRADE_WORDS if len(w) < 8]

# Foreign legal forms: such awardees aren't registered with the SEC or DTI, so a registry
# lookup won't settle the pair. GMBH, PTE and LLC are additions beyond the original rule.
FOREIGN_FORM = re.compile(r"\b(LIMITED|LTD|PTY|GMBH|PTE|LLC)\b", re.I)

NEXT_ACTION = {
    "same_entity": "merge",
    "same_or_successor_likely": "merge; confirm with SEC/DTI lookup if amount is material",
    "check": "registry lookup on both names",
    "related_likely": "keep separate; link as related; pull GIS for shared officers",
    "related_check": "keep separate; pull GIS/DTI owner before linking",
    "same_parent_unit": "keep units separate; roll up to parent for totals",
    "unrelated_likely": "keep separate",
}


def _is_trade_word(token):
    return (token in TRADE_WORDS
            or any(SequenceMatcher(None, token, w).ratio() >= 0.85 for w in LONG_TRADE_WORDS)
            or any(SequenceMatcher(None, token, w).ratio() >= 0.9 for w in SHORT_TRADE_WORDS))


def core(name):
    """Name core: 2-R CONSTRUCTION & SUPPLY -> 2R, AL NIÑO -> ALNINO."""
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().upper()
    name = name.replace("&AMP;", " AND ").replace("&", " AND ").replace("+", " PLUS ")
    name = re.sub(r"[^A-Z0-9 ]", " ", name)
    tokens = [t for t in name.split() if t not in LEGAL_FORMS and not _is_trade_word(t)]
    tokens = [t[:-1] if len(t) > 3 and t.endswith("S") else t for t in tokens]
    return "".join(tokens)


def _years(text):
    parts = [int(p) for p in re.findall(r"\d{4}", text or "")]
    return (parts[0], parts[-1]) if parts else None


def year_handoff(years_a, years_b):
    a, b = _years(years_a), _years(years_b)
    if not a or not b:
        return False
    first, later = sorted((a, b))
    return abs(later[0] - first[1]) <= 2 and later[1] > first[1]


def is_branch(name_a, name_b, same_core):
    names = (name_a.upper(), name_b.upper())
    return any("BRANCH" in n for n in names) or (any(" - " in n for n in names) and not same_core)


def label(f):
    """Rule cascade; the first match wins. DPWH contractor IDs override other signals."""
    if f["dpwh"] == "same_id":
        return "same_entity"
    if f["branch"] and f["sim"] >= 0.6 and f["shared"] > 0:
        return "same_parent_unit"
    contains = f["core_a"] in f["core_b"] or f["core_b"] in f["core_a"]
    if not f["same_core"] and (contains or f["sim"] >= 0.75) and f["shared"] >= 3 and f["overlap"] >= 0.30:
        return "related_check"
    if f["dpwh"] == "different_id":
        if f["same_core"] and (f["shared"] > 0 or f["handoff"]):
            return "related_likely"
        if f["same_core"] or (f["sim"] >= 0.85 and f["shared"] >= 3):
            return "related_check"
        return "unrelated_likely"
    if f["same_core"] and (f["shared"] > 0 or f["handoff"]):
        return "same_or_successor_likely"
    if f["same_core"] or (f["sim"] >= 0.85 and f["shared"] > 0):
        return "check"
    return "unrelated_likely"


def score(f):
    points = 0
    if f["dpwh"] == "same_id":
        points += 5
    if f["dpwh"] == "mixed_ids":
        points += 1
    if f["same_core"]:
        points += 2
    elif f["sim"] >= 0.85:
        points += 1
    if f["shared"] >= 3 or f["overlap"] >= 0.30:
        points += 2
    elif f["shared"] > 0:
        points += 1
    if f["handoff"]:
        points += 1
    return points


def evidence_text(f, row, relation):
    parts = []
    if f["dpwh"] == "same_id":
        parts.append("same DPWH contractor ID")
    elif f["dpwh"] == "different_id":
        parts.append("different DPWH IDs (separate registrations)")
    elif f["dpwh"] == "mixed_ids":
        parts.append("DPWH maps a name to 2+ IDs")
    if f["same_core"]:
        parts.append("identical name core")
    elif f["sim"] >= 0.85:
        parts.append(f"name core near-identical ({f['core_a']} vs {f['core_b']})")
    else:
        parts.append(f"name core differs ({f['core_a']} vs {f['core_b']})")
    if f["shared"] == 0:
        parts.append("no shared buyers")
    elif f["shared"] >= 3 or f["overlap"] >= 0.30:
        parts.append(f"{f['shared']} shared buyers ({f['overlap_exact']:.0%} of smaller)")
    else:
        parts.append(f"{f['shared']} shared buyer(s)")
    if f["handoff"]:
        parts.append(f"name handoff {row.get('years_a', '')} / {row.get('years_b', '')}")
    if FOREIGN_FORM.search(row["name_a"]) or FOREIGN_FORM.search(row["name_b"]):
        parts.append("foreign entity: not in SEC/DTI")
    if relation == "same_parent_unit":
        parts.append("branches or sub-units of one parent organization")
    if relation == "related_check" and not f["same_core"]:
        parts.append("different name but heavy buyer overlap: possible related party")
    return "; ".join(parts)


def triage(row):
    """Triage columns for one review row (a dict from match_awardees.py)."""
    core_a, core_b = core(row["name_a"]), core(row["name_b"])
    shared = int(row.get("shared_organizations") or 0)
    smaller = min(int(row.get("organizations_a") or 0), int(row.get("organizations_b") or 0))
    same_core = core_a == core_b and len(core_a) >= 2
    f = {
        "core_a": core_a, "core_b": core_b, "same_core": same_core,
        "sim": round(SequenceMatcher(None, core_a, core_b).ratio(), 3),
        "shared": shared, "overlap": round(shared / max(1, smaller), 3), "overlap_exact": shared / max(1, smaller),
        "handoff": year_handoff(row.get("years_a"), row.get("years_b")),
        "dpwh": row.get("dpwh_evidence") or "none",
    }
    f["branch"] = is_branch(row["name_a"], row["name_b"], same_core)
    relation = label(f)
    return {
        "name_core_a": core_a, "name_core_b": core_b, "core_similarity": f["sim"],
        "buyer_overlap": f["overlap"], "year_handoff": f["handoff"], "evidence_score": score(f),
        "relation_label": relation, "relation_evidence": evidence_text(f, row, relation),
        "next_action": NEXT_ACTION[relation],
    }
