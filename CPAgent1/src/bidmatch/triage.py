"""Value-based triage: over_20k flag and value_band string."""

from dataclasses import dataclass
from typing import Optional

DEFAULT_VALUE_CEILING = 5_000_000.0


@dataclass(frozen=True)
class Triage:
    est_total_value: Optional[float]
    over_20k: str
    value_band: str
    ceiling_flag: str  # "" | "value over ceiling - review"


def compute(
    qty_value: Optional[float],
    unit_price: Optional[float],
    total_magnitude_estimate: Optional[float] = None,
    ceiling: float = DEFAULT_VALUE_CEILING,
) -> Triage:
    """Decide over_20k + value_band.

    Priority for est_total_value:
      1. total_magnitude_estimate (median of prior award totals/amounts —
         pre-computed magnitude that bypasses qty multiplication)
      2. qty_value * unit_price (when both known)
      3. Unknown

    Any estimate above `ceiling` is flagged and routed to Unknown so it
    doesn't silently inflate Over 20k.
    """
    if total_magnitude_estimate is not None:
        total = round(total_magnitude_estimate, 2)
    elif qty_value is not None and unit_price is not None:
        total = round(qty_value * unit_price, 2)
    else:
        flag = (
            "no quantity - cannot scale"
            if unit_price is not None and qty_value is None
            else ""
        )
        return Triage(
            est_total_value=None,
            over_20k="Unknown",
            value_band="Unknown",
            ceiling_flag=flag,
        )

    if total > ceiling:
        return Triage(
            est_total_value=total,
            over_20k="Unknown",
            value_band="Unknown",
            ceiling_flag="value over ceiling - review",
        )

    if total >= 80_000:
        band = "Above $80K"
    elif total >= 20_000:
        band = "$20K-$80K"
    else:
        band = "Below $20K"
    return Triage(
        est_total_value=total,
        over_20k="Yes" if total >= 20_000 else "No",
        value_band=band,
        ceiling_flag="",
    )
