#!/usr/bin/env python3
"""
parse_email.py
==============
Parse a bidmatch email into a structured opportunity list.

Handles the format observed in real bidmatch newsletters, e.g.:

    10 -- Suppressors 5.56 NATO (JUSTICE, DEPARTMENT OF, FBI-JEH ...)
    25 -- BRACKET,VEHICULAR C 9EA NSN 2590017012434 SOL SPE7L026T0325
          PR 7016916362 DUE 06/11/26 AMSC G (Defense Logistics Agency (DLA))
    34 - Continuous Welded Rail (Illinois - METRA )

Accepts either a plain-text .txt or a .eml/.mbox file (extracts the text body).

Emits a list[dict] with these keys (missing fields = None):

    fsc            FSC/PSC code (leading number, e.g. "10", "25", "95")
    title          Title text of the article
    agency         Agency in parentheses at end of line
    solicitation   Solicitation number after "SOL"          (e.g. SPE7L026T0325)
    nsn            NSN after "NSN"                          (13-digit)
    pr_number      PR number after "PR"
    due_date       Response date after "DUE" (mm/dd/yy)
    amsc           AMSC letter after "AMSC" (single letter)
    quantity       Quantity (e.g. "9EA", "10KT", "6PM") if present
    raw            The original line

Solicitation numbers are the strongest search key for SAM.gov. When one is
absent, downstream code falls back to a title-based search.
"""

from __future__ import annotations

import argparse
import email
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional


# ------------------------- regex library -------------------------

# Solicitation numbers: alnum, typically 13-17 chars starting with a letter.
# Real examples in the wild:
#   SPE7L026T0325, SPE7LX26D0059, SPE7L326U0564, W91CRB-25-Q-0053, N00104-26-R-2001
_SOL_RE = re.compile(r"\bSOL\s+([A-Z0-9-]{8,20})\b", re.I)
# Also match well-known DoD contract patterns even without the "SOL" marker:
_SOL_INLINE_RE = re.compile(
    r"\b(SPE[A-Z0-9]{10,14}"                                 # DLA (SPE + 10-14 alnum)
    r"|W[0-9A-Z]{5}-[0-9]{2}-[A-Z]-[0-9]{4}"                 # Army
    r"|N[0-9]{5}-[0-9]{2}-[A-Z]-[0-9]{4}"                    # Navy
    r"|FA[0-9]{4}-[0-9]{2}-[A-Z]-[0-9]{4})\b"                # Air Force
)
_NSN_RE = re.compile(r"\bNSN[:\s]+([0-9]{4}[-\s]?[0-9]{2}[-\s]?[0-9]{3}[-\s]?[0-9]{4})\b", re.I)
_PR_RE = re.compile(r"\bPR\s+([0-9]{8,12})\b", re.I)
_DUE_RE = re.compile(r"\bDUE\s+(\d{1,2}/\d{1,2}/\d{2,4})\b", re.I)
_AMSC_RE = re.compile(r"\bAMSC\s+([A-Z])\b")
_QTY_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s?(EA|KT|PM|LB|OZ|FT|YD|IN|CM|MM|BX|CS|DZ|GAL|GRO|HD|KG|MT|PR|RM|SE|SL|ST|TN|TU|VL)\b")
_FSC_RE = re.compile(r"^\s*(\d{2,4})\s*-{1,2}\s*(.+)$")

# Split off the trailing "(Agency)" — accepting nested parens: "(DoD (DLA))".
def _split_title_agency(s: str) -> tuple[str, Optional[str]]:
    s = s.rstrip()
    if not s.endswith(")"):
        return s.strip(), None
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        c = s[i]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                title = s[:i].rstrip()
                agency = s[i + 1 : -1].strip()
                return title.strip(), agency
    return s.strip(), None


# ------------------------- dataclass -------------------------

@dataclass
class Opportunity:
    raw: str
    fsc: Optional[str] = None
    title: Optional[str] = None
    agency: Optional[str] = None
    solicitation: Optional[str] = None
    nsn: Optional[str] = None
    pr_number: Optional[str] = None
    due_date: Optional[str] = None
    amsc: Optional[str] = None
    quantity: Optional[str] = None

    def search_key(self) -> Optional[str]:
        """Best key for the SAM.gov search — solicitation# first, else title."""
        return self.solicitation or (self.title or None)


# ------------------------- parser -------------------------

def _iter_lines(text: str) -> Iterable[str]:
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # HTML entities from stray mail-to-text conversions:
        line = line.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'")
        yield line


def parse_line(line: str) -> Optional[Opportunity]:
    """Parse a single bidmatch line into an Opportunity, or None if it isn't one."""
    m = _FSC_RE.match(line)
    if not m:
        return None
    fsc, rest = m.group(1), m.group(2)

    title, agency = _split_title_agency(rest)

    op = Opportunity(raw=line, fsc=fsc, title=title, agency=agency)

    # Structured fields (search the WHOLE rest, since some appear inside the title body).
    sol = _SOL_RE.search(rest)
    if sol:
        op.solicitation = sol.group(1).upper()
    else:
        # inline (no SOL keyword) — but reject if it's clearly in the agency chunk
        inline = _SOL_INLINE_RE.search(title or "")
        if inline:
            op.solicitation = inline.group(1).upper()

    nsn = _NSN_RE.search(rest)
    if nsn:
        op.nsn = re.sub(r"[\s-]", "", nsn.group(1))

    pr = _PR_RE.search(rest)
    if pr:
        op.pr_number = pr.group(1)

    due = _DUE_RE.search(rest)
    if due:
        op.due_date = due.group(1)

    amsc = _AMSC_RE.search(rest)
    if amsc:
        op.amsc = amsc.group(1)

    qty = _QTY_RE.search(title or "")
    if qty:
        op.quantity = f"{qty.group(1)}{qty.group(2)}"

    # Trim the structured tail out of the display title, if any of it was there:
    if op.solicitation or op.nsn or op.pr_number or op.due_date or op.amsc:
        trimmed = re.split(
            r"\s+\d+(?:[.,]\d+)?(?:EA|KT|PM|LB|OZ|FT|YD|IN|CM|MM|BX|CS|DZ|GAL|GRO|HD|KG|MT|PR|RM|SE|SL|ST|TN|TU|VL)\s+NSN\b",
            title or "", 1, flags=re.I,
        )[0].rstrip()
        # If the split didn't fire, drop trailing "NSN ... AMSC X" tail if present:
        trimmed = re.sub(r"\s+NSN\s.*?AMSC\s+[A-Z]\s*$", "", trimmed).rstrip()
        if trimmed:
            op.title = trimmed

    return op


def parse_email_text(text: str) -> list[Opportunity]:
    out: list[Opportunity] = []
    for line in _iter_lines(text):
        op = parse_line(line)
        if op:
            out.append(op)
    return out


def _email_body_text(path: Path) -> str:
    """Return best-effort plain text from a .eml/.mbox or .txt file."""
    data = path.read_bytes()
    if data[:6] in (b"From: ", b"Subjec") or path.suffix.lower() in (".eml", ".mbox"):
        try:
            msg = email.message_from_bytes(data)
            for part in msg.walk() if msg.is_multipart() else [msg]:
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            # fallback: html -> naive strip
            for part in msg.walk() if msg.is_multipart() else [msg]:
                if part.get_content_type() == "text/html":
                    html = (part.get_payload(decode=True) or b"").decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    return re.sub(r"<[^>]+>", " ", html)
        except Exception:
            pass
    return data.decode("utf-8", errors="replace")


# ------------------------- CLI -------------------------

def main():
    ap = argparse.ArgumentParser(description="Parse a bidmatch email into opportunities.json")
    ap.add_argument("email", help="Path to a .txt/.eml/.mbox bidmatch email")
    ap.add_argument("--out", default="-", help="Output path (default: stdout)")
    args = ap.parse_args()

    text = _email_body_text(Path(args.email))
    ops = parse_email_text(text)
    print(f"parsed {len(ops)} opportunities from {args.email}", file=sys.stderr)

    payload = [asdict(o) for o in ops]
    if args.out == "-":
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
