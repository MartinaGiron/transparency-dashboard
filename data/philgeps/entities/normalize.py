"""
Name normalization for matching PhilGEPS awardee names.

Each name gets three keys, from strict to loose:
    clean_key  case, punctuation, spacing, HTML entities and common abbreviations
               normalized. Names with the same clean_key are treated as the same
               spelling of one name and merged automatically.
    core_key   clean_key without trailing legal-form words (INC, CORP, OPC, ...).
               Same core_key means "probably the same company"; sent to review.
    loose_key  core_key without trailing business-type words (TRADING,
               ENTERPRISES, GENERAL MERCHANDISE, ...). These can be different
               businesses with the same owner; sent to review.

Notes such as "(FORMERLY: ...)" or "DOING BUSINESS UNDER THE NAME AND STYLE OF ..."
are cut from the keys and returned separately so reviewers can see them.
"""

import html
import re
import unicodedata

# Expanded token by token, after punctuation is removed.
ABBREVIATIONS = {
    "CONST": "CONSTRUCTION",
    "CONSTN": "CONSTRUCTION",
    "CONSTRN": "CONSTRUCTION",
    "DEVT": "DEVELOPMENT",
    "DEV": "DEVELOPMENT",
    "BLDRS": "BUILDERS",
    "BLDR": "BUILDER",
    "ENGG": "ENGINEERING",
    "ENGRG": "ENGINEERING",
    "MDSE": "MERCHANDISE",
    "GEN": "GENERAL",
    "PHILS": "PHILIPPINES",
    "INCORPORATED": "INC",
    "CORPORATION": "CORP",
    "COMPANY": "CO",
    "LIMITED": "LTD",
    "ENT": "ENTERPRISES",
    "ENTERPRISE": "ENTERPRISES",
    "ENTERPRISESS": "ENTERPRISES",
    "COOP": "COOPERATIVE",
}

LEGAL_FORMS = {"INC", "CORP", "CO", "LTD", "OPC"}

# Multi-word phrases are matched as token sequences at the end of the name.
BUSINESS_TYPES = [
    ("GENERAL", "MERCHANDISE"),
    ("TRADING",),
    ("ENTERPRISES",),
    ("SERVICES",),
    ("SUPPLY",),
    ("SUPPLIES",),
    ("AND",),  # left over from e.g. "... CONSTRUCTION AND SUPPLY"
]

ANNOTATION_PATTERNS = [
    re.compile(r"\(\s*FORMERLY\b.*?\)", re.I),
    re.compile(r"\bFORMERLY\b\s*:?.*$", re.I),
    re.compile(r"\bDOING BUSINESS (UNDER|AS)\b.*$", re.I),
    re.compile(r"\(\s*OWNED (AND|&) (REGISTERED|OPERATED) BY\b.*?\)", re.I),
]

_ENTITY = re.compile(r"&([A-Za-z]+|#[0-9]+|#x[0-9A-Fa-f]+);")


def decode_entities(name: str) -> str:
    """Decode HTML entities, including uppercase ones like &AMP; and &NBSP;."""
    return html.unescape(_ENTITY.sub(lambda m: "&" + m.group(1).lower() + ";", name))


def split_annotations(name: str) -> tuple[str, list[str]]:
    """Return the name without notes like (FORMERLY: ...), and the notes removed."""
    notes = []
    for pattern in ANNOTATION_PATTERNS:
        for match in pattern.finditer(name):
            notes.append(match.group(0).strip())
        name = pattern.sub(" ", name)
    return name, notes


def _strip_accents(name: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))


def _join_initials(tokens: list[str]) -> list[str]:
    """J T GAMOLO -> JT GAMOLO, so "J.T." and "JT" match."""
    out, in_run = [], False
    for t in tokens:
        initial = len(t) == 1 and t.isalpha()
        if initial and in_run:
            out[-1] += t
        else:
            out.append(t)
        in_run = initial
    return out


def _tokens(name: str) -> list[str]:
    name = _strip_accents(decode_entities(name)).upper()
    name = name.replace("&", " AND ")
    name = re.sub(r"[^A-Z0-9 ]", " ", name.replace("'", "").replace(".", " "))
    return [ABBREVIATIONS.get(t, t) for t in _join_initials(name.split())]


def _strip_trailing(tokens: list[str], phrases: list[tuple[str, ...]]) -> list[str]:
    changed = True
    while changed and tokens:
        changed = False
        for phrase in phrases:
            n = len(phrase)
            if len(tokens) > n and tuple(tokens[-n:]) == phrase:
                tokens = tokens[:-n]
                changed = True
    return tokens


def name_keys(name: str) -> dict:
    """Return clean_key, core_key, loose_key and any notes cut from the name."""
    main, notes = split_annotations(decode_entities(name or ""))
    tokens = _tokens(main)
    clean = " ".join(tokens)
    core_tokens = _strip_trailing(tokens, [(t,) for t in LEGAL_FORMS])
    core = " ".join(core_tokens)
    loose = " ".join(_strip_trailing(core_tokens, BUSINESS_TYPES))
    return {"clean_key": clean, "core_key": core, "loose_key": loose, "notes": notes}


JV_PATTERN = re.compile(r"(JOINT? VENTURE|\bJ\.?\s?V\.?\s*$|\(\s*J\.?\s?V\.?\s*\)|[-/ (]JV\b|\bCONSORTIUM\b)", re.I)
JV_FALSE_POSITIVES = re.compile(r"\bJV (&|AND) CO|JVS\b|JVTM|\bJV (GENERAL|TRADING|ENTERPRISES?)\b", re.I)


def is_joint_venture(name: str) -> bool:
    """True for names with an explicit joint-venture marker (handled separately)."""
    name = decode_entities(name or "")
    return bool(JV_PATTERN.search(name)) and not JV_FALSE_POSITIVES.search(name)
