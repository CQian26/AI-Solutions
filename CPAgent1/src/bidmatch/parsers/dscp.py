"""DSCP / DLA-style article parser — deep extraction."""

import re
from typing import Dict
from bs4 import BeautifulSoup

NSN_RE = re.compile(r"\b(\d{4}-\d{2}-\d{3}-\d{4})\b")
NSN_DIGITS_RE = re.compile(r"\bNSN\s+(\d{13})\b", re.IGNORECASE)
SOL_RE = re.compile(r"\b(SP[A-Z0-9]{11})\b")
PR_RE = re.compile(r"\bPR[:\s]+([0-9]{6,12})\b", re.IGNORECASE)
DUE_RE = re.compile(r"DUE\s*DATE[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
AMSC_RE = re.compile(r"\bAMSC[:\s]+([A-Z])\b", re.IGNORECASE)
NOM_LABEL_RE = re.compile(r"\bNOMENCLATURE[:\s]+([^\n\r]+)", re.IGNORECASE)
# Strip leading FSG prefix(es) and capture nomenclature up to the first of:
#   "<qty><unit> NSN" (DSCP compact form),
#   "NSN <digits>"   (digits inline form),
#   "SOL <code>".
NOM_LEADING_RE = re.compile(
    r"^\s*\d{1,3}\s*-+\s*(?:\d{1,3}-+)?(.+?)\s+(?:\d{1,6}[A-Z]{2}\s+NSN|NSN\s+\d|SOL\s)",
    re.IGNORECASE | re.MULTILINE,
)
QTY_RE = re.compile(r"\bQUANTITY[:\s]+([^\n\r]+)", re.IGNORECASE)
# Inline RFQ-format quantity: "Line 0001 Qty 3      UI EA"
QTY_INLINE_RE = re.compile(
    r"\bQty\s+([\d,]+)\s+UI\s+([A-Z]+)", re.IGNORECASE
)
# DSCP compact format: "48EA NSN 3120010767021" — qty digits + 2-letter unit
# attached (no space), immediately before "NSN <13 digits>"
QTY_COMPACT_RE = re.compile(
    r"\b(\d{1,6})([A-Z]{2})\s+NSN\s+\d{13}\b", re.IGNORECASE
)
QTY_SPLIT_RE = re.compile(r"^([\d,\.]+)\s*([A-Za-z]+)?")
NAICS_RE = re.compile(r"\bNAICS[:\s]+(\d{2,6})", re.IGNORECASE)
PSC_RE = re.compile(r"\bPSC[:\s]+([A-Z0-9]{4})", re.IGNORECASE)
SET_ASIDE_RE = re.compile(r"\bSET[\s-]*ASIDE[:\s]+([^\n\r]+)", re.IGNORECASE)
APEX_RE = re.compile(r"\bAPEX\s*CONTACT[:\s]+([^\n\r]+)", re.IGNORECASE)
DESC_RE = re.compile(r"\bDescription:\s*([^\n\r]+)", re.IGNORECASE)
RFQ_PDF_RE = re.compile(
    r"(https?://dibbs2\.bsm\.dla\.mil/Downloads/RFQ/[^\s<>\"']+\.PDF)",
    re.IGNORECASE,
)
PACKAGE_VIEW_RE = re.compile(
    r"(https?://dibbs\.bsm\.dla\.mil/rfq/rfqrec\.aspx\?sn=[^\s<>\"']+)",
    re.IGNORECASE,
)


def _first(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


_URL_ARTIFACT_RE = re.compile(r"\s*\bURL:?\s*$", re.IGNORECASE)


def _clean_set_aside(raw: str) -> str:
    """Strip the trailing 'URL:' link-label artifact the portal HTML leaves
    on the set-aside line. A value that was only the artifact becomes ''."""
    return _URL_ARTIFACT_RE.sub("", (raw or "").strip()).strip()


def _normalize_nsn(raw: str) -> str:
    """Convert a 13-digit NSN to dashed FSC-NIIN form (4-2-3-4).
    Passes through already-dashed NSN unchanged. Returns "" on garbage.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if NSN_RE.fullmatch(s):
        return s
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) != 13:
        return ""
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:9]}-{digits[9:13]}"


def _split_qty(raw: str) -> tuple[float | None, str]:
    if not raw:
        return None, ""
    m = QTY_SPLIT_RE.match(raw.strip())
    if not m:
        return None, ""
    num_str = m.group(1).replace(",", "")
    try:
        value = float(num_str)
    except ValueError:
        return None, ""
    unit = (m.group(2) or "").strip().upper()
    return value, unit


def _extract_nsn(text: str) -> str:
    """Try labeled-dashed → labeled-digits → bare-dashed → bare-digits."""
    m = NSN_RE.search(text)
    if m:
        return m.group(1)
    m = NSN_DIGITS_RE.search(text)
    if m:
        return _normalize_nsn(m.group(1))
    return ""


def _extract_qty(text: str) -> tuple[str, float | None, str]:
    """Try `QUANTITY: <raw>` → inline `Qty <n> UI <unit>` → compact `<n><unit> NSN`."""
    raw = _first(QTY_RE, text)
    if raw:
        value, unit = _split_qty(raw)
        if value is not None:
            return raw, value, unit
    m = QTY_INLINE_RE.search(text)
    if m:
        num_str = m.group(1).replace(",", "")
        try:
            value = float(num_str)
            unit = m.group(2).strip().upper()
            return f"{num_str} {unit}", value, unit
        except ValueError:
            pass
    m = QTY_COMPACT_RE.search(text)
    if m:
        num_str = m.group(1).replace(",", "")
        try:
            value = float(num_str)
            unit = m.group(2).strip().upper()
            return f"{num_str} {unit}", value, unit
        except ValueError:
            pass
    return raw, None, ""


def _nomenclature(text: str) -> str:
    nom = _first(NOM_LABEL_RE, text)
    if nom:
        return nom
    return _first(NOM_LEADING_RE, text)


def parse_dscp(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    qty_raw, qty_value, qty_unit = _extract_qty(text)

    return {
        "nomenclature": _nomenclature(text),
        "qty_raw": qty_raw,
        "qty_value": qty_value,
        "qty_unit": qty_unit,
        "nsn": _extract_nsn(text),
        "solicitation_number": _first(SOL_RE, text),
        "pr_number": _first(PR_RE, text),
        "due_date": _first(DUE_RE, text),
        "amsc": _first(AMSC_RE, text),
        "naics": _first(NAICS_RE, text),
        "psc": _first(PSC_RE, text),
        "set_aside": _clean_set_aside(_first(SET_ASIDE_RE, text)),
        "apex_contact": _first(APEX_RE, text),
        "description": _first(DESC_RE, text),
        "rfq_pdf_url": _first(RFQ_PDF_RE, text),
        "package_view_url": _first(PACKAGE_VIEW_RE, text),
    }
