"""Bid / No Bid / Investigate decision engine + restriction detection."""

import re
from typing import List, Tuple

_APPROVED_SOURCE_DESC_RE = re.compile(
    r"\bapproved\s+source|source\s+control(?:led)?\b", re.IGNORECASE
)
_RESTRICTIVE_RISKS = {"medium", "high"}


def detect_approved_source(
    amsc: str, amsc_meaning: str, amsc_risk: str, description: str
) -> str:
    """Return a short human string when a source restriction is detected."""
    if amsc and amsc_risk in _RESTRICTIVE_RISKS:
        return f"AMSC {amsc} — {amsc_meaning}"
    if description and _APPROVED_SOURCE_DESC_RE.search(description):
        return "approved source in description"
    return ""


BID_FLOOR = 20_000.0

# Order matters: qualified categories are checked before the generic
# small-business match ("SDVOSB ... Small Business ..." must NOT read as
# plain small business).
_SET_ASIDE_CATEGORIES = [
    ("sdvosb", re.compile(r"service[\s-]*disabled|veteran", re.I)),
    ("hubzone", re.compile(r"hub\s*zone", re.I)),
    ("wosb", re.compile(r"women[\s-]*owned|\bwosb\b|\bedwosb\b", re.I)),
    ("8a", re.compile(r"8\s*\(\s*a\s*\)", re.I)),
    ("small_business", re.compile(r"small\s+business", re.I)),
]
_ALWAYS_ALIGNED_RE = re.compile(r"unrestricted|no set-asides confirmed", re.I)


def classify_set_aside(set_aside: str, cp_set_asides: List[str]) -> str:
    sa = (set_aside or "").strip()
    if not sa:
        return "blank"
    if _ALWAYS_ALIGNED_RE.search(sa):
        return "aligned"
    for category, pattern in _SET_ASIDE_CATEGORIES:
        if pattern.search(sa):
            return "aligned" if category in cp_set_asides else "misaligned"
    return "unrecognized"


def decide(
    est_total_value,
    value_confidence: str,
    set_aside: str,
    approved_source: str,
    ceiling_flag: str,
    cp_set_asides: List[str],
) -> Tuple[str, str]:
    if ceiling_flag:
        return "Investigate", ceiling_flag
    align = classify_set_aside(set_aside, cp_set_asides)
    if value_confidence == "High" and est_total_value is not None:
        fails = []
        if est_total_value < BID_FLOOR:
            fails.append(f"below ${BID_FLOOR:,.0f} floor")
        if align == "misaligned":
            fails.append(f"set-aside mismatch: {set_aside}")
        if approved_source:
            fails.append(approved_source)
        if fails:
            return "No Bid", "; ".join(fails)
        if align == "unrecognized":
            return "Investigate", f"unrecognized set-aside: {set_aside}"
        return "Bid", "value + confidence pass; no restrictions detected"
    if est_total_value is None:
        return "Investigate", "no price estimate — manual pricing required"
    return "Investigate", f"{value_confidence or 'No'} confidence — needs manual review"
