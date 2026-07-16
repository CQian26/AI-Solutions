"""SAM.gov metadata client. Gated on SAM_API_KEY.

Returns a small dict with solicitation metadata only:
  set_aside, response_deadline, sam_notice_id, title, active

If no API key is provided, returns {} cleanly so the rest of the
pipeline proceeds with empty SAM fields. SAM.gov does NOT contribute
to price-confidence scoring; metadata only.
"""

from typing import Dict, Optional
import requests

from bidmatch.http_client import USER_AGENT

API_URL = "https://api.sam.gov/opportunities/v2/search"
TIMEOUT = 30.0


def fetch_metadata(solicitation_number: str, api_key: Optional[str]) -> Dict[str, str]:
    if not api_key:
        return {}
    if not solicitation_number:
        return {}
    params = {
        "api_key": api_key,
        "solnum": solicitation_number,
        "limit": 1,
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        r = requests.get(API_URL, params=params, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
        rows = body.get("opportunitiesData") or []
        if not rows:
            return {}
        row = rows[0]
        return {
            "set_aside": row.get("typeOfSetAside") or "",
            "response_deadline": row.get("responseDeadLine") or "",
            "sam_notice_id": row.get("noticeId") or "",
            "title": row.get("title") or "",
            "active": row.get("active") or "",
        }
    except Exception:
        return {}


def search_award_amounts(nsn_digits: str, api_key: Optional[str]) -> list:
    """Search SAM award notices mentioning the NSN; return magnitude rows.

    Tier-5 evidence: award amount + date, shaped like USASpending rows
    ({amount, action_date}) so the aggregator reuses the same math.
    Expected low hit rate on DLA micro-buys. [] on any failure.
    """
    if not api_key or not nsn_digits:
        return []
    params = {
        "api_key": api_key,
        "q": nsn_digits,
        "ptype": "a",  # award notices only
        "limit": 10,
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        r = requests.get(API_URL, params=params, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        rows = r.json().get("opportunitiesData") or []
        out = []
        for row in rows:
            award = row.get("award") or {}
            try:
                amount = float(str(award.get("amount", "")).replace(",", ""))
            except ValueError:
                continue
            if amount > 0:
                out.append({
                    "amount": amount,
                    "action_date": award.get("date") or "",
                })
        return out
    except Exception:  # noqa: BLE001 — metadata tier, never blocks pipeline
        return []
