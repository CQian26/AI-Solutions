"""Phase B — DIBBS RFQ retrieval. STUB. Default OFF.

DIBBS RFQ packages can reference export-controlled technical data subject
to ITAR (22 CFR 120-130). This module must NOT auto-download or parse
those packages without explicit human confirmation of the drawing's
distribution statement and ITAR classification.

This module is intentionally not wired into the Phase A pipeline.
"""

from pathlib import Path


class DibbsDisabledError(RuntimeError):
    """Raised when DIBBS retrieval is invoked while disabled."""


class DibbsITARNotice(RuntimeError):
    """Raised when retrieval is enabled but ITAR ack has not been given."""


_ITAR_NOTICE = (
    "\n"
    "==============================================================\n"
    "  DIBBS RFQ RETRIEVAL — ITAR / EXPORT CONTROL NOTICE\n"
    "==============================================================\n"
    "  DLA DIBBS RFQ packages can reference technical data whose\n"
    "  distribution is restricted under ITAR (22 CFR 120-130) or\n"
    "  Distribution Statements B-F.\n"
    "\n"
    "  Before any controlled document is retrieved, a human must\n"
    "  confirm:\n"
    "    1. The drawing's distribution statement, and\n"
    "    2. That the recipient is authorized to receive that data.\n"
    "\n"
    "  This stub will NOT download. Re-run with\n"
    "  itar_acknowledged=True only after the above is confirmed.\n"
    "==============================================================\n"
)


def retrieve_rfq_pdf(
    rfq_pdf_url: str,
    dest_dir: str | Path,
    enabled: bool = False,
    itar_acknowledged: bool = False,
    pdf_password: str | None = None,
) -> Path:
    if not enabled:
        raise DibbsDisabledError(
            "DIBBS retrieval is disabled. Pass enabled=True only after "
            "reviewing the ITAR notice."
        )
    if not itar_acknowledged:
        print(_ITAR_NOTICE)
        raise DibbsITARNotice(
            "ITAR acknowledgement required before retrieval."
        )
    # Intentionally not implemented in the demo.
    raise NotImplementedError(
        "Phase B retrieval is stubbed. Implement DIBBS auth + "
        "password-protected PDF open here (pypdf accepts a password)."
    )
