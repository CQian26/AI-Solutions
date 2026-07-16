"""Per-solicitation compliance-extraction wrapper.

Called once per Agent 2 Handoff row that carries a DIBBS solicitation
number. Idempotent (skips the browser walk when the requirements CSV
already exists on disk) and defensive (Playwright missing, network
failures, and scanned PDFs are logged, not raised).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("bidmatch.compliance")

DIBBS_SOL_RE = re.compile(r"^SP[A-Z0-9]{11,13}$")


@dataclass(frozen=True)
class ComplianceResult:
    solicitation: str
    status: str
    requirements_csv: Optional[Path]
    clause_count: int
    error: str = ""


def is_dibbs_solicitation(sol: str) -> bool:
    """Return True when `sol` looks like a DLA/DIBBS solicitation.

    DLA sols start with SP and are 13–15 chars total. Anything else
    (state RFQs, non-DoD, blank) is skipped — the extractor only knows
    how to walk DIBBS.
    """
    if not sol:
        return False
    return bool(DIBBS_SOL_RE.match(sol.strip().upper()))


def extract_for_solicitation(
    sol: str,
    outdir: Path,
    *,
    force: bool = False,
) -> ComplianceResult:
    """Extract compliance clauses for one DIBBS solicitation.

    - Returns a status="skipped" result if the sol is not DIBBS-shaped.
    - Returns cached result (status="cached") if the CSV already exists
      and `force` is False — avoids launching Chromium for every rerun.
    - Any exception (Playwright missing, network, etc.) is caught and
      surfaced as status="error"; the pipeline continues.
    """
    sol_norm = (sol or "").strip().upper()
    if not is_dibbs_solicitation(sol_norm):
        return ComplianceResult(sol_norm, "skipped", None, 0,
                                error="not a DIBBS solicitation")

    outdir = Path(outdir)
    csv_path = outdir / f"{sol_norm}_requirements.csv"
    if csv_path.exists() and not force:
        clauses = _count_clauses_in_csv(csv_path)
        log.info("compliance cache hit sol=%s (%d clauses)", sol_norm, clauses)
        return ComplianceResult(sol_norm, "cached", csv_path, clauses)

    try:
        from bidmatch.compliance import dibbs_extractor
    except (ImportError, SystemExit) as e:
        log.warning("compliance extractor unavailable: %s", e)
        return ComplianceResult(sol_norm, "error", None, 0, error=str(e))

    log.info("compliance extract sol=%s -> %s", sol_norm, outdir)
    try:
        result = dibbs_extractor.extract(sol_norm, outdir, silent=True)
    except SystemExit as e:
        msg = f"extractor exited (playwright missing?): code={e.code}"
        log.warning(msg)
        return ComplianceResult(sol_norm, "error", None, 0, error=msg)
    except Exception as e:
        log.warning("compliance extract failed sol=%s: %s", sol_norm, e)
        return ComplianceResult(sol_norm, "error", None, 0, error=str(e))

    status = result.get("status", "error")
    if status != "ok":
        return ComplianceResult(sol_norm, status, None, 0,
                                error=result.get("error", ""))

    findings = result.get("findings", {})
    clause_count = sum(len(v) for v in findings.values())
    return ComplianceResult(
        sol_norm, "ok", result.get("requirements_csv"), clause_count,
    )


def _count_clauses_in_csv(path: Path) -> int:
    try:
        with open(path, newline="") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0
