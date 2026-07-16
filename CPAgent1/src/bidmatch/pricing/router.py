"""Route a solicitation to its best-fit pricing source.

Returns:
  "dla"     — any SP* prefix; DIBBS is the primary High-tier source
  "service" — W*, N*, FA*; USASpending primary (Medium/Low)
  "other"   — empty or unrecognised; USASpending best-effort
"""


def route(solicitation_number: str, nsn: str) -> str:
    sol = (solicitation_number or "").upper().strip()
    if sol[:2] == "SP":
        return "dla"
    if sol[:1] in ("W", "N") or sol[:2] == "FA":
        return "service"
    return "other"
