"""DIBBS award-document PDF parser — tier-2 unit prices.

When the current RFQ's Section A has no history, prior awards' own award
documents (public PDFs on dibbs2) carry the awarded UNIT PRICE and QUANTITY.
We fetch up to `max_pdfs` of the most recent per-buy awards and parse their
CLIN rows.

URL pattern (confirmed by live recon, Task 2):
    https://dibbs2.bsm.dla.mil/Downloads/Awards/<DDMONYY>/<AWARD>.PDF
where <DDMONYY> is the AWARD DATE formatted %d%b%y uppercased (e.g. award
date 2014-10-14 -> "14OCT14"), and the extension is uppercase .PDF.

Public data only. Award documents are public; we never touch cFolders or
technical data packages. Award PDFs are immutable — cache them forever.
"""

import io
import re
from datetime import datetime
from typing import Dict, List

import requests

from bidmatch.http_client import USER_AGENT
from bidmatch.pricing.dibbs_section_a import DIBBS2_BASE, _accept_dibbs2_banner

# A CLIN row in DLA award documents, as actually captured in
# tests/fixtures/award_pdf_sample.txt:
#   ITEM NO.  SUPPLIES/SERVICES  QUANTITY          UNIT  UNIT PRICE         AMOUNT
#   0001      1005- 01-044-6074   26.000            EA    $ 10,388.60000     $ 270,103.60
# <4-digit CLIN>  <NSN, may contain internal spaces>  <qty float>  <2-letter unit>
#   $ <unit price>  $ <amount>
_CLIN_ROW_RE = re.compile(
    r"^\s*\d{4}\s+.+?\s+([\d,]+\.\d{1,3})\s+([A-Z]{2})\s+\$\s*([\d,]+\.\d{2,5})\s+\$\s*([\d,]+\.\d{2})",
    re.MULTILINE,
)


def _award_pdf_url_for(award_number: str, award_date_iso: str) -> str:
    """Build the dibbs2 award-PDF URL. Returns '' if either input is empty or
    the date doesn't parse as %Y-%m-%d."""
    if not award_number or not award_date_iso:
        return ""
    try:
        d = datetime.strptime(award_date_iso, "%Y-%m-%d")
    except ValueError:
        return ""
    seg = d.strftime("%d%b%y").upper()
    return f"{DIBBS2_BASE}/Downloads/Awards/{seg}/{award_number}.PDF"


def parse_award_pdf_text(text: str) -> List[Dict]:
    rows: List[Dict] = []
    for m in _CLIN_ROW_RE.finditer(text):
        try:
            qty = float(m.group(1).replace(",", ""))
            unit_price = float(m.group(3).replace(",", ""))
        except ValueError:
            continue
        if qty > 0 and unit_price > 0:
            rows.append({"quantity": qty, "unit_price": unit_price})
    return rows


def _extract_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes or pdf_bytes[:5] != b"%PDF-":
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # noqa: BLE001
        return ""


def fetch_award_unit_prices(
    awards: List[Dict],
    session: requests.Session | None = None,
    max_pdfs: int = 3,
) -> List[Dict]:
    """Fetch award PDFs for the most recent awards; return unit-price rows.

    `awards` come from dibbs_awards.fetch_awards (already IDC-filtered by the
    caller). Returns [] on any failure; partial results are fine.
    """
    if not awards:
        return []
    s = session or requests.Session()
    s.headers.setdefault("User-Agent", USER_AGENT)
    recent = sorted(awards, key=lambda a: a.get("award_date") or "", reverse=True)
    out: List[Dict] = []
    for award in recent[:max_pdfs]:
        num = award.get("award_number") or ""
        url = _award_pdf_url_for(num, award.get("award_date") or "")
        if not url:
            continue
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 200 and r.content[:5] != b"%PDF-":
                _accept_dibbs2_banner(s, url[len(DIBBS2_BASE):])
                r = s.get(url, timeout=30)
            if r.status_code != 200 or r.content[:5] != b"%PDF-":
                continue
            for row in parse_award_pdf_text(_extract_text(r.content)):
                out.append({
                    "award_number": num,
                    "award_date": award.get("award_date") or "",
                    **row,
                })
        except Exception:  # noqa: BLE001 — best-effort tier; skip bad award
            continue
    return out
