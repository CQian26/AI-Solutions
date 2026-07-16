"""USASpending REST API client.

Three queries supported per NSN:
  - NSN-text search: NSN string in award description (Medium tier)
  - NAICS+PSC search: category proxy (Low tier, legacy)
  - PSC-only search: product-class proxy derived from NSN FSC (Low tier)

All return a normalised list of rows.
"""

from datetime import date, timedelta
from typing import Dict, List

import requests

from bidmatch.http_client import USER_AGENT

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
CONTRACT_TYPES = ["A", "B", "C", "D"]
DEFAULT_LOOKBACK_YEARS = 5
TIMEOUT = 30.0


def _time_window(years: int = DEFAULT_LOOKBACK_YEARS) -> Dict[str, str]:
    today = date.today()
    return {
        "start_date": (today - timedelta(days=years * 365)).isoformat(),
        "end_date": today.isoformat(),
    }


def _build_nsn_payload(nsn_digits: str) -> Dict:
    return {
        "filters": {
            "keywords": [nsn_digits],
            "time_period": [_time_window()],
            "award_type_codes": CONTRACT_TYPES,
        },
        "fields": [
            "Award ID", "Recipient Name", "Award Amount",
            "Description", "Action Date", "NAICS Code",
        ],
        "page": 1,
        "limit": 25,
        "sort": "Award Amount",
        "order": "desc",
    }


def _build_naics_psc_payload(naics: str, psc: str) -> Dict:
    return {
        "filters": {
            "naics_codes": [naics],
            "psc_codes": [psc],
            "time_period": [_time_window()],
            "award_type_codes": CONTRACT_TYPES,
        },
        "fields": [
            "Award ID", "Recipient Name", "Award Amount",
            "Description", "Action Date", "NAICS Code",
        ],
        "page": 1,
        "limit": 25,
        "sort": "Award Amount",
        "order": "desc",
    }


def _normalise(rows: List[Dict], nsn_digits: str | None) -> List[Dict]:
    out: List[Dict] = []
    for r in rows or []:
        desc = (r.get("Description") or "")
        out.append({
            "award_id": r.get("Award ID") or "",
            "recipient": r.get("Recipient Name") or "",
            "amount": float(r.get("Award Amount") or 0.0),
            "description": desc,
            "action_date": r.get("Action Date") or "",
            "naics": r.get("NAICS Code") or "",
            "nsn_in_description": bool(nsn_digits and nsn_digits in desc),
        })
    return out


def _post(payload: Dict) -> List[Dict]:
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
        return body.get("results") or []
    except Exception:
        return []


def query_by_nsn_text(nsn_digits: str) -> List[Dict]:
    if not nsn_digits:
        return []
    raw = _post(_build_nsn_payload(nsn_digits))
    return _normalise(raw, nsn_digits)


def query_by_naics_psc(naics: str, psc: str) -> List[Dict]:
    if not naics or not psc:
        return []
    raw = _post(_build_naics_psc_payload(naics, psc))
    return _normalise(raw, nsn_digits=None)


# Drop the largest awards (program-level multi-year contracts) from PSC searches
# so the category proxy stays anchored to per-buy magnitudes.
PSC_AMOUNT_CAP = 1_000_000.0


def _build_psc_keyword_payload(psc: str, keyword: str) -> Dict:
    filters = {
        "psc_codes": [psc],
        "time_period": [_time_window()],
        "award_type_codes": CONTRACT_TYPES,
        # Cap per-award amount to filter out program-level mega-contracts.
        "award_amounts": [{"lower_bound": 1, "upper_bound": PSC_AMOUNT_CAP}],
    }
    if keyword:
        filters["keywords"] = [keyword]
    return {
        "filters": filters,
        "fields": [
            "Award ID", "Recipient Name", "Award Amount",
            "Description", "Action Date", "NAICS Code",
        ],
        "page": 1,
        "limit": 25,
        "sort": "Award Amount",
        "order": "desc",
    }


def query_by_psc(psc: str, keyword: str = "") -> List[Dict]:
    """Query USASpending by PSC with an optional nomenclature keyword.

    Always filters out awards above PSC_AMOUNT_CAP ($1M) so program-level
    multi-year contracts don't pollute the per-buy magnitude estimate.
    """
    if not psc:
        return []
    raw = _post(_build_psc_keyword_payload(psc, keyword))
    return _normalise(raw, nsn_digits=None)
