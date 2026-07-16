"""DIBBS award-history scraper (best-effort, public unauthenticated).

Flow per NSN:
  1. GET /dodwarning.aspx?goto=<target> — receive ASP.NET consent form
  2. POST that page with all hidden inputs + butAgree=OK — receive session cookie
  3. GET /Awards/AwdRecs.aspx?Category=nsn&Tab=Awd&Value=<digits>

`Category=nsn` is the NSN-keyed lookup that returns historical awards.
`Category=apo` was the wrong choice — it queries Active Purchase Order
(the current open solicitation), which by definition has no awards yet.

The DIBBS search results table contains TOTAL contract price per award,
not per-unit price. Aggregator must use totals as magnitude signals, not
multiply by current-opportunity quantity.
"""

import re
from datetime import datetime
from typing import Dict, List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from bidmatch.http_client import USER_AGENT

DIBBS_BASE = "https://www.dibbs.bsm.dla.mil"
AWARDS_PATH = "/Awards/AwdRecs.aspx"


def _digits_only(nsn: str) -> str:
    return "".join(c for c in (nsn or "") if c.isdigit())


_MONEY_RE = re.compile(r"[^\d.]")
_AWARD_NUM_RE = re.compile(r"^[A-Z0-9]{8,}")
# Split award-number cell text on any non-printable-ASCII character
_NON_ASCII_RE = re.compile(r"[^\x20-\x7E]+")


def _parse_money(s: str) -> float | None:
    if not s:
        return None
    cleaned = _MONEY_RE.sub("", s)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(s: str) -> str:
    """Parse a DIBBS award date. DIBBS uses MM-DD-YYYY in search results."""
    s = (s or "").strip()
    for fmt in ("%m-%d-%Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _extract_award_number(cell) -> str:
    """Extract the award number from cell[1].

    The real DIBBS HTML encodes the award number in a span followed by
    non-ASCII bytes and trailing link text (e.g. 'Award/Basic Package View').
    We split on non-ASCII and take the first clean segment.
    """
    raw = cell.get_text(strip=True)
    # Split on any non-printable-ASCII run; first part is the award number
    parts = _NON_ASCII_RE.split(raw)
    return parts[0].strip() if parts else raw.strip()


# Column indices in the real DIBBS AwdRecs results table (13 columns)
_COL_AWARD_NUMBER    = 1
_COL_DELIVERY_ORDER  = 2
_COL_AWARDEE_CAGE    = 5
_COL_TOTAL_PRICE     = 6
_COL_AWARD_DATE      = 7
_COL_NSN             = 9
_COL_SOLICITATION    = 12


def parse_awards_page(html: str) -> List[Dict]:
    """Parse the DIBBS Award Search Results HTML into a list of awards.

    Each award dict has:
        award_number, delivery_order_number, awardee_cage, award_date (ISO),
        total, nsn, solicitation
    Note: `unit_price` and `qty` are NOT available from the search-result
    table; only Total Contract Price. Aggregator handles this distinction.
    """
    if "No Awards are on file" in html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    awards: List[Dict] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 13:
            continue
        award_num = _extract_award_number(cells[_COL_AWARD_NUMBER])
        # Heuristic: real award numbers are alphanumeric and 8+ chars long.
        # Filters header and non-data rows.
        if not _AWARD_NUM_RE.match(award_num):
            continue
        awards.append({
            "award_number":          award_num,
            "delivery_order_number": cells[_COL_DELIVERY_ORDER].get_text(strip=True),
            "awardee_cage":          cells[_COL_AWARDEE_CAGE].get_text(strip=True),
            "award_date":            _parse_date(cells[_COL_AWARD_DATE].get_text(strip=True)),
            "total":                 _parse_money(cells[_COL_TOTAL_PRICE].get_text(strip=True)),
            "nsn":                   cells[_COL_NSN].get_text(strip=True),
            "solicitation":          cells[_COL_SOLICITATION].get_text(strip=True),
        })
    return awards


def _accept_dod_warning(session: requests.Session, target_path: str) -> None:
    warn_url = f"{DIBBS_BASE}/dodwarning.aspx?goto={quote(target_path)}"
    r = session.get(warn_url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    if not form:
        return
    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        t = inp.get("type")
        if t in ("hidden", "submit"):
            data[name] = inp.get("value") or ""
    session.post(warn_url, data=data, timeout=30)


def fetch_awards(nsn: str, session: requests.Session | None = None) -> List[Dict]:
    """Fetch DIBBS awards for an NSN. Returns [] on any failure."""
    digits = _digits_only(nsn)
    if not digits:
        return []
    s = session or requests.Session()
    s.headers.setdefault("User-Agent", USER_AGENT)
    target = f"{AWARDS_PATH}?Category=nsn&Tab=Awd&Value={digits}"
    try:
        _accept_dod_warning(s, target)
        r = s.get(f"{DIBBS_BASE}{target}", timeout=30)
        if r.status_code != 200:
            return []
        return parse_awards_page(r.text)
    except (requests.RequestException, ValueError):
        return []
