"""Pricing aggregator (v3 — six-tier waterfall).

Combines outputs from the RFQ PDF's Section A per-buy history, DIBBS
award-document PDFs, the DIBBS Awards-page totals scraper, USASpending
NSN-in-description matches, SAM.gov award notices, and USASpending PSC
category matches into a single unit-price decision with confidence
tier and explicit value_basis labeling.

Tier rules:
  High   (tier 1) = DIBBS Section A per-buy unit price on this NSN
  High   (tier 2) = DIBBS award-document PDF unit prices
  Medium (tier 3) = DIBBS Awards-page per-buy award TOTALS (magnitude)
  Medium (tier 4) = USASpending NSN-in-description, ≤5y (magnitude)
  Medium (tier 5) = SAM.gov award notices matching NSN (magnitude)
  Low    (tier 6) = USASpending PSC category match (NEVER promotes)
  None            = no comparable awards

Only per-unit sources (tiers 1-2) set est_unit_price with
total_magnitude_estimate=None. Magnitude sources (tiers 3-6) set
total_magnitude_estimate with est_unit_price=None. Empty/unusable data
at any tier falls through to the next tier — nothing is fabricated.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Dict, List, Optional, Tuple

LOOKBACK_YEARS = 5

PER_BUY_TYPES = {"F", "T"}  # Firm-fixed-price + Spot awards
CEILING_TYPES = {"D", "A", "B"}  # IDC + Long-term + BPA


def _is_per_buy(award: Dict) -> bool:
    """An award is per-buy if its instrument-type code at position 9 is F/T,
    or if it has a delivery_order_number (real DO against an IDC ceiling).

    DLA contract numbering: positions 0-1 = "SP", 2-7 = office + year + julian,
    position 8 = instrument type (F, T, D, A, B, M, etc.), 9-12 = sequence.
    """
    do = (award.get("delivery_order_number") or "").strip()
    if do and do not in ("", "\xa0"):
        return True
    award_num = (award.get("award_number") or "").upper()
    if len(award_num) < 9:
        return False
    return award_num[8] in PER_BUY_TYPES


@dataclass(frozen=True)
class PriceResult:
    est_unit_price: Optional[float]
    n_observations: int
    price_range: Optional[Tuple[float, float]]
    latest_award_date: str
    price_source: str
    value_confidence: str
    value_basis: str
    price_query: str
    # Pre-computed total magnitude (median of prior award totals).
    # Triage uses this directly when set; otherwise falls back to
    # qty_value * est_unit_price. Section A leaves this None (est_unit_price
    # is populated instead so triage multiplies by current qty); USA
    # Medium/Low tiers set it as an award-level magnitude proxy.
    total_magnitude_estimate: Optional[float] = None


def _within_lookback(action_date_iso: str) -> bool:
    try:
        d = datetime.strptime(action_date_iso, "%Y-%m-%d").date()
    except ValueError:
        return False
    cutoff = date.today() - timedelta(days=LOOKBACK_YEARS * 365)
    return d >= cutoff


def _aggregate_dibbs_section_a(awards: List[Dict]) -> PriceResult:
    units = [a["unit_price"] for a in awards if a.get("unit_price")]
    dates = [a.get("award_date") or "" for a in awards if a.get("award_date")]
    recent_units = [
        a["unit_price"] for a in awards
        if a.get("unit_price") and _within_lookback(a.get("award_date") or "")
    ]
    if recent_units:
        unit = median(recent_units)
        basis = (
            f"DIBBS Section A per-buy award history: median unit price of "
            f"{len(recent_units)} prior awards (last 5 years). Real per-unit "
            "anchor; prior award qty may differ from this solicitation."
        )
    elif units:
        unit = median(units)
        basis = (
            f"DIBBS Section A: median of {len(units)} prior unit prices "
            "(all >5 years old). Use as legacy reference only."
        )
    else:
        unit = None
        basis = "DIBBS Section A returned rows but no parseable unit prices"
    return PriceResult(
        est_unit_price=round(unit, 2) if unit is not None else None,
        n_observations=len(units),
        price_range=(min(units), max(units)) if units else None,
        latest_award_date=max(dates) if dates else "",
        price_source="DIBBS Section A",
        value_confidence="High" if unit is not None else "None",
        value_basis=basis,
        price_query="",
        total_magnitude_estimate=None,
    )


def _aggregate_award_pdfs(rows: List[Dict]) -> PriceResult:
    """High tier from prior awards' own award-document PDFs."""
    units = [r["unit_price"] for r in rows if r.get("unit_price")]
    dates = [r.get("award_date") or "" for r in rows if r.get("award_date")]
    recent = [
        r["unit_price"] for r in rows
        if r.get("unit_price") and _within_lookback(r.get("award_date") or "")
    ]
    pick = recent or units
    unit = median(pick) if pick else None
    basis = (
        f"DIBBS award documents: median unit price of {len(pick)} awarded "
        "CLIN rows" + ("" if recent else " (all >5 years old — legacy reference)")
        + ". Real per-unit anchor from executed awards."
    ) if unit is not None else "award PDFs fetched but no parseable CLIN rows"
    return PriceResult(
        est_unit_price=round(unit, 2) if unit is not None else None,
        n_observations=len(units),
        price_range=(min(units), max(units)) if units else None,
        latest_award_date=max(dates) if dates else "",
        price_source="DIBBS Award PDF",
        value_confidence="High" if unit is not None else "None",
        value_basis=basis,
        price_query="",
        total_magnitude_estimate=None,
    )


def _aggregate_dibbs_totals(awards: List[Dict]) -> PriceResult:
    """Medium tier: Awards-page TOTAL contract prices (no qty)."""
    per_buy = [a for a in awards if _is_per_buy(a)]
    n_ceiling = len(awards) - len(per_buy)
    totals = [a["total"] for a in per_buy if a.get("total")]
    dates = [a.get("award_date") or "" for a in per_buy if a.get("award_date")]
    recent = [
        a["total"] for a in per_buy
        if a.get("total") and _within_lookback(a.get("award_date") or "")
    ]
    pick = recent or totals
    rep = median(pick) if pick else None
    note = f" Excluded {n_ceiling} ceiling/IDC contract(s)." if n_ceiling else ""
    basis = (
        f"DIBBS Awards page: median of {len(pick)} per-buy award TOTALS "
        "(no quantity available — award magnitude, not a unit price)."
        + ("" if recent else " All >5 years old.") + note
    ) if rep is not None else (
        "DIBBS returned only ceiling/IDC awards; no per-buy comparable." + note
    )
    return PriceResult(
        est_unit_price=None,
        n_observations=len(per_buy),
        price_range=(min(totals), max(totals)) if totals else None,
        latest_award_date=max(dates) if dates else "",
        price_source="DIBBS",
        value_confidence="Medium" if rep is not None else "None",
        value_basis=basis,
        price_query="",
        total_magnitude_estimate=round(rep, 2) if rep is not None else None,
    )


def _aggregate_sam_awards(rows: List[Dict]) -> PriceResult:
    amounts = [r["amount"] for r in rows if r.get("amount")]
    dates = [r.get("action_date") or "" for r in rows]
    rep = median(amounts) if amounts else None
    return PriceResult(
        est_unit_price=None,
        n_observations=len(rows),
        price_range=(min(amounts), max(amounts)) if amounts else None,
        latest_award_date=max(dates) if dates else "",
        price_source="SAM.gov award",
        value_confidence="Medium" if rep is not None else "None",
        value_basis=(
            f"SAM.gov award notices matching NSN: median of {len(amounts)} "
            "award amounts. Award-level magnitude, NOT a unit price."
        ),
        price_query="",
        total_magnitude_estimate=round(rep, 2) if rep is not None else None,
    )


def _aggregate_usa_nsn(matches: List[Dict]) -> PriceResult:
    amounts = [m["amount"] for m in matches if m.get("amount")]
    dates = [m.get("action_date") or "" for m in matches]
    recent_amounts = [
        m["amount"] for m in matches
        if m.get("amount") and _within_lookback(m.get("action_date") or "")
    ]
    if recent_amounts:
        rep = median(recent_amounts)
        scope = f"{len(recent_amounts)} recent (≤5y) award totals"
    elif amounts:
        rep = median(amounts)
        scope = f"{len(amounts)} award totals (all >5y)"
    else:
        rep = None
        scope = "no parseable amounts"
    return PriceResult(
        est_unit_price=None,
        n_observations=len(matches),
        price_range=(min(amounts), max(amounts)) if amounts else None,
        latest_award_date=max(dates) if dates else "",
        price_source="USASpending NSN-in-desc",
        value_confidence="Medium" if rep is not None else "None",
        value_basis=(
            f"USASpending NSN-in-description (fuzzy match): median of {scope}. "
            "Award-level magnitude, NOT a unit price. Reviewer must judge "
            "whether prior award qty resembles this opportunity's qty."
        ),
        price_query="",
        total_magnitude_estimate=round(rep, 2) if rep is not None else None,
    )


def _aggregate_usa_psc(matches: List[Dict], query_summary: str) -> PriceResult:
    amounts = [m["amount"] for m in matches if m.get("amount")]
    dates = [m.get("action_date") or "" for m in matches]
    recent_amounts = [
        m["amount"] for m in matches
        if m.get("amount") and _within_lookback(m.get("action_date") or "")
    ]
    if recent_amounts:
        rep = median(recent_amounts)
        scope = f"{len(recent_amounts)} recent (≤5y) category awards"
    elif amounts:
        rep = median(amounts)
        scope = f"{len(amounts)} category awards (all >5y)"
    else:
        rep = None
        scope = "no parseable amounts"
    return PriceResult(
        est_unit_price=None,
        n_observations=len(matches),
        price_range=(min(amounts), max(amounts)) if amounts else None,
        latest_award_date=max(dates) if dates else "",
        price_source="USASpending PSC",
        value_confidence="Low" if rep is not None else "None",
        value_basis=(
            f"PSC-anchored category proxy: median of {scope}. NOT a part "
            "price — lumps different items in the same product class. Use "
            "as a sanity-check magnitude only."
        ),
        price_query=query_summary,
        total_magnitude_estimate=round(rep, 2) if rep is not None else None,
    )


def aggregate(
    dibbs_section_a_awards: Optional[List[Dict]] = None,
    award_pdf_rows: Optional[List[Dict]] = None,
    dibbs_award_totals: Optional[List[Dict]] = None,
    usa_nsn_matches: Optional[List[Dict]] = None,
    sam_award_rows: Optional[List[Dict]] = None,
    usa_psc_matches: Optional[List[Dict]] = None,
    query_summary: str = "",
) -> PriceResult:
    tiers = [
        (dibbs_section_a_awards, _aggregate_dibbs_section_a),
        (award_pdf_rows, _aggregate_award_pdfs),
        (dibbs_award_totals, _aggregate_dibbs_totals),
        (usa_nsn_matches, _aggregate_usa_nsn),
        (sam_award_rows, _aggregate_sam_awards),
    ]
    fallback = None
    for data, fn in tiers:
        if data:
            result = fn(data)
            if result.value_confidence != "None":
                return result
            fallback = fallback or result   # keep first informative failure note
    if usa_psc_matches:
        return _aggregate_usa_psc(usa_psc_matches, query_summary)
    if fallback is not None:
        return fallback
    return PriceResult(
        est_unit_price=None, n_observations=0, price_range=None,
        latest_award_date="", price_source="", value_confidence="None",
        value_basis="no comparable awards", price_query=query_summary,
    )
