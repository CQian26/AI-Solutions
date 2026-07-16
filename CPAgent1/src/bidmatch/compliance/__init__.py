"""DIBBS compliance extraction — pulls compliance clauses per solicitation.

Wraps the standalone `dibbs_extractor` script so the BidMatch pipeline can
call it once per DIBBS solicitation number and attach the resulting
requirements CSV back onto each Agent 2 Handoff row.
"""

from bidmatch.compliance.runner import (
    ComplianceResult,
    extract_for_solicitation,
)

__all__ = ["ComplianceResult", "extract_for_solicitation"]
