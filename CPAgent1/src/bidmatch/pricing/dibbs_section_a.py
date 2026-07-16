"""DIBBS RFQ PDF Section A parser — per-buy unit prices from prior awards.

The DIBBS Awards search page only shows Total Contract Price. The actual
per-unit prices live in Section A ("Procurement History for NSN/FSC:...")
of each RFQ PDF. This module fetches the PDF from dibbs2.bsm.dla.mil
(after accepting that host's DoD consent banner), extracts the text with
pypdf, and parses the prior-award rows.

Public data only. We read the public RFQ PDF; we don't click into
controlled technical data packages or drawings.
"""

import io
import re
from datetime import datetime
from typing import Dict, List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from bidmatch.http_client import USER_AGENT

DIBBS2_BASE = "https://dibbs2.bsm.dla.mil"

# A prior-award row in Section A. Whitespace-separated columns:
#   CAGE   Contract Number      Quantity   Unit Cost    AWD Date  Surplus
# Example: 9Y957  SPE4A625V592J           42.000    341.00000  20250827  N
_ROW_RE = re.compile(
    r"^\s*([A-Z0-9]{5})\s+"          # CAGE
    r"([A-Z0-9]{10,17})\s+"          # Contract number
    r"([\d,]+\.?\d*)\s+"              # Quantity
    r"([\d,]+\.?\d*)\s+"              # Unit cost
    r"(\d{8})\s+"                     # Award date YYYYMMDD
    r"([YN])\s*$",                    # Surplus indicator
    re.MULTILINE,
)


def _pdf_url_for(sol: str) -> str:
    """Build the dibbs2 PDF URL for a solicitation. Returns '' if input is empty."""
    if not sol:
        return ""
    return f"{DIBBS2_BASE}/Downloads/RFQ/{sol[-1]}/{sol}.pdf"


def _parse_yyyymmdd(s: str) -> str:
    try:
        return datetime.strptime(s, "%Y%m%d").date().isoformat()
    except ValueError:
        return ""


def parse_section_a(text: str) -> List[Dict]:
    """Parse the prior-award table out of PDF-extracted text."""
    awards: List[Dict] = []
    for m in _ROW_RE.finditer(text):
        try:
            qty = float(m.group(3).replace(",", ""))
            unit_price = float(m.group(4).replace(",", ""))
        except ValueError:
            continue
        awards.append({
            "cage": m.group(1),
            "contract_number": m.group(2),
            "quantity": qty,
            "unit_price": unit_price,
            "award_date": _parse_yyyymmdd(m.group(5)),
            "surplus": m.group(6),
        })
    return awards


def extract_section_a_from_pdf(pdf_bytes: bytes) -> List[Dict]:
    """Read PDF bytes with pypdf and parse Section A."""
    if not pdf_bytes or pdf_bytes[:5] != b"%PDF-":
        return []
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
    except Exception:  # noqa: BLE001
        return []
    return parse_section_a(text)


def _accept_dibbs2_banner(session: requests.Session, target_path: str) -> None:
    """Accept the dibbs2 DoD consent banner. Follows the chained POST to set
    both required cookies (TS01xxx + dw). Must be called once per session
    before any GET against dibbs2.bsm.dla.mil."""
    warn_url = f"{DIBBS2_BASE}/dodwarning.aspx?goto={quote(target_path)}"
    r = session.get(warn_url, timeout=30)
    if r.status_code != 200:
        return
    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:  # noqa: BLE001
        return
    form = soup.find("form")
    if not form:
        return
    data = {
        inp.get("name"): (inp.get("value") or "")
        for inp in form.find_all("input")
        if inp.get("name") and inp.get("type") in ("hidden", "submit")
    }
    session.headers["Referer"] = warn_url
    # allow_redirects=True is critical: the chained acceptance sets the
    # second 'dw' cookie. Stopping at the first redirect leaves the cookie
    # unset and subsequent GETs return the banner page.
    session.post(warn_url, data=data, timeout=30, allow_redirects=True)


def fetch_section_a_awards(
    sol: str,
    session: requests.Session | None = None,
) -> List[Dict]:
    """Top-level: accept banner, fetch the PDF, return parsed awards. [] on any error."""
    if not sol:
        return []
    s = session or requests.Session()
    s.headers.setdefault("User-Agent", USER_AGENT)
    url = _pdf_url_for(sol)
    if not url:
        return []
    target_path = url[len(DIBBS2_BASE):]  # /Downloads/RFQ/<last>/<sol>.pdf
    try:
        # First attempt: maybe session is already authorised
        r = s.get(url, timeout=30)
        if r.status_code != 200:
            return []
        if r.content[:5] != b"%PDF-":
            # We got the banner. Accept it and retry once.
            _accept_dibbs2_banner(s, target_path)
            r = s.get(url, timeout=30)
            if r.status_code != 200 or r.content[:5] != b"%PDF-":
                return []
        return extract_section_a_from_pdf(r.content)
    except Exception:  # noqa: BLE001 — docstring promises [] on any failure
        return []
