"""Procure-source article parser — best-effort deep extraction."""

import re
from typing import Dict
from bs4 import BeautifulSoup

from bidmatch.parsers.dscp import (
    NSN_RE, SOL_RE, _split_qty, _extract_nsn, _extract_qty, _normalize_nsn,
    NOM_LEADING_RE, _clean_set_aside,
)

NOM_LABEL_RE = re.compile(
    r"\bNomenclature:\s+([^\n\r]+?)(?:\s{2,}|$)", re.IGNORECASE
)
QTY_LABEL_RE = re.compile(
    r"\bQuantity:\s+([^\n\r]+?)(?:\s{2,}|$)", re.IGNORECASE
)
NSN_LABEL_RE = re.compile(
    r"\bNSN:\s+(\d{4}-\d{2}-\d{3}-\d{4})", re.IGNORECASE
)
NAICS_LABEL_RE = re.compile(
    r"\bNAICS(?:\s*Code)?:\s+(\d{2,6})", re.IGNORECASE
)
PSC_LABEL_RE = re.compile(
    r"\bPSC(?:\s*Code)?:\s+([A-Z0-9]{4})", re.IGNORECASE
)
SET_ASIDE_LABEL_RE = re.compile(
    r"\bSet[\s-]*Aside\s*(?:Type)?:\s+([^\n\r]+?)(?:\s{2,}|$)", re.IGNORECASE
)
APEX_RE = re.compile(
    r"\bAPEX\s*Contact:\s+([^\n\r]+?)(?:\s{2,}|$)", re.IGNORECASE
)
CLOSE_DATE_RE = re.compile(
    r"Solicitation\s+will\s+close\s+on\s+or\s+about[:\s]+"
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
INLINE_DUE_RE = re.compile(
    r"\bDUE\s+(\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
ARTICLE_NUMBER_RE = re.compile(
    r"OutreachSystems\s+Article\s+Number:\s+(\S+)", re.IGNORECASE
)
DESC_HEADING_RE = re.compile(
    r"<h4>([^<]+)</h4>", re.IGNORECASE | re.DOTALL
)


def _first(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _nomenclature(text: str) -> str:
    nom = _first(NOM_LABEL_RE, text)
    if nom:
        return nom
    return _first(NOM_LEADING_RE, text)


def parse_procure(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    # Inline RFQ format wins when present; falls back to QTY_LABEL_RE pattern
    qty_inline_raw, qty_inline_value, qty_inline_unit = _extract_qty(text)
    qty_label_raw = _first(QTY_LABEL_RE, text)
    if qty_label_raw:
        qty_label_value, qty_label_unit = _split_qty(qty_label_raw)
        qty_raw = qty_label_raw
        qty_value = qty_label_value
        qty_unit = qty_label_unit
    else:
        qty_raw, qty_value, qty_unit = qty_inline_raw, qty_inline_value, qty_inline_unit

    # NSN: labeled → inline digits → bare dashed
    nsn = _first(NSN_LABEL_RE, text) or _extract_nsn(text)
    sol = _first(SOL_RE, text)
    due = _first(CLOSE_DATE_RE, text) or _first(INLINE_DUE_RE, text)

    description = _first(DESC_HEADING_RE, html)

    return {
        "nomenclature": _nomenclature(text),
        "qty_raw": qty_raw,
        "qty_value": qty_value,
        "qty_unit": qty_unit,
        "nsn": nsn,
        "solicitation_number": sol,
        "pr_number": "",
        "due_date": due,
        "amsc": "",
        "naics": _first(NAICS_LABEL_RE, text),
        "psc": _first(PSC_LABEL_RE, text),
        "set_aside": _clean_set_aside(_first(SET_ASIDE_LABEL_RE, text)),
        "apex_contact": _first(APEX_RE, text),
        "description": description,
        "outreach_article_number": _first(ARTICLE_NUMBER_RE, text),
        "rfq_pdf_url": "",
        "package_view_url": "",
    }
