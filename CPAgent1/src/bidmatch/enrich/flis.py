"""FLIS technical-data enrichment.

STUB. The columns exist in the workbook for forward-compatibility, but
the values are blank with provenance noting that a PUBLOG account is
pending. See docs/superpowers/specs/2026-06-17-bidmatch-v3-design.md §3
for the pre-flight findings that drove this decision.
"""

from typing import Dict, Tuple

FLIS_KEYS: Tuple[str, ...] = (
    "material",
    "dimensions",
    "surface_treatment",
    "criticality",
    "hazmat_indicator",
    "precious_metals_indicator",
    "demil_code",
    "export_classification",
    "approved_supplier_present",
    "demand_forecast_value",
    "flis_source",
    "flis_provenance",
)


def lookup(nsn: str) -> Dict[str, str]:
    out = {k: "" for k in FLIS_KEYS}
    out["flis_source"] = "PUBLOG (pending account)"
    out["flis_provenance"] = "from PUBLOG, pending account"
    return out
