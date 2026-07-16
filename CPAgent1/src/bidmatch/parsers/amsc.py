"""AMSC (Acquisition Method Suffix Code) decoder.

Codes are documented at https://www.dla.mil/Portals/104/Documents/J3PolicyAndAcquisition/AMC-AMSC.pdf
This is a small subset covering the codes most often seen on DLA / DIBBS
opportunities; unknown codes return a safe default with risk="unknown".

Risk semantics:
- low    = full govt rights, competitive procurement, no special restriction
- medium = govt rights with caveats (e.g. proprietary parts, restricted competition)
- high   = restricted technical data, sole-source likely, ITAR/export sensitive
"""

from typing import Dict, Tuple

AMSC_CODES: Dict[str, Tuple[str, str]] = {
    "G": ("Full government data rights; competitive procurement", "low"),
    "K": ("Government has rights; data adequate for competition", "low"),
    "B": ("Government has rights; competitive after qualification", "low"),
    "H": ("Government has rights but proprietary; restricted competition", "medium"),
    "C": ("Manufacturer-proprietary data; OEM-restricted", "medium"),
    "D": ("Data with restrictions; competition may be limited", "medium"),
    "P": ("Restricted technical data; sole source likely", "high"),
    "R": ("Restricted; OEM only", "high"),
    "Y": ("Source-restricted by safety/security/quality", "high"),
    "T": ("Must be acquired from a manufacturer-approved source", "medium"),
}


def decode(code: str) -> Dict[str, str]:
    raw = (code or "").strip().upper()
    if not raw:
        return {"amsc": "", "amsc_meaning": "", "amsc_risk": "unknown"}
    meaning, risk = AMSC_CODES.get(raw, ("Unknown AMSC code", "unknown"))
    return {"amsc": raw, "amsc_meaning": meaning, "amsc_risk": risk}
