#!/usr/bin/env python3
"""
run.py — the pipeline
=====================
End-to-end supplier scorecard pipeline:

    bidmatch email  ->  parse_email.py       (opportunities)
                    ->  sam_client.py         (SAM.gov v2 search + attachment URLs)
                    ->  attachment_scanner.py (PDF/DOCX -> text)
                    ->  clause_extractor.py   (FAR/DFARS citations)
                    ->  scorecard_writer.py   (.xlsx with 3 sheets)

Usage
-----
    # LIVE against sam.gov (needs a free api.data.gov key):
    export SAM_API_KEY=xxxxxxxxxxxx
    python3 run.py samples_from_email/bidmatch_email_1.txt \
        --out output/supplier_scorecard.xlsx

    # OFFLINE demo run (no network, no key):
    python3 run.py samples_from_email/bidmatch_email_1.txt \
        --mock-dir sample/mock_sam \
        --out output/supplier_scorecard.xlsx

    # Limit to the first N opportunities (useful during dev):
    python3 run.py samples_from_email/bidmatch_email_1.txt --limit 5 ...

Skips lines without an obvious solicitation# OR clean title (avoids
polluting the search with noisy DLA truncations like "10--COVER ASSEMBLY,MACH").
Use --include-untitled to force scanning of every line anyway.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path


def _banner_stderr(*lines: str) -> None:
    """Print an eye-catching warning banner to stderr."""
    width = max(72, max((len(l) for l in lines), default=0) + 4)
    bar = "!" * width
    print("\n" + bar, file=sys.stderr)
    for l in lines:
        print(f"!! {l}".ljust(width - 2) + "!!", file=sys.stderr)
    print(bar + "\n", file=sys.stderr)

from parse_email import parse_email_text, _email_body_text, Opportunity
from sam_client import SamClient, SamClientError, Notice
from attachment_scanner import extract_text_from_file
from clause_extractor import extract_clauses, clauses_to_dicts, extract_standards, standards_to_dicts
from scorecard_writer import write_scorecard

log = logging.getLogger("scorecard")

# Lines with these title patterns are DLA truncations that will confuse text
# search. Skip them unless the line also carries a solicitation number.
_TRUNCATED_TITLE_RE = re.compile(r"^\d{2,}--", re.I)


def _should_search(op: Opportunity) -> bool:
    if op.solicitation:
        return True
    title = (op.title or "").strip()
    if not title or _TRUNCATED_TITLE_RE.match(title):
        return False
    return True


def _process_notice(
    op: Opportunity,
    notice: Notice,
    sam: SamClient,
    attach_dir: Path,
    *,
    scan_attachments: bool,
    is_mock: bool = False,
) -> dict:
    """Run extractors against a notice and return the per-contract result dict."""
    sources = ["description"]
    text_blobs = [notice.description or ""]

    if scan_attachments and notice.attachments:
        for att in notice.attachments:
            local = attach_dir / (
                f"{op.solicitation or notice.notice_id}__{Path(att.name).name}"
            )
            try:
                if att.url.startswith(("http://", "https://")):
                    sam.download(att.url, local)
                else:
                    src = Path(att.url)
                    if not src.is_absolute():
                        src = Path.cwd() / src
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_bytes(src.read_bytes())
                text = extract_text_from_file(local)
                if text.strip():
                    text_blobs.append(text)
                    sources.append(f"attachment:{Path(att.name).name}")
                    log.info("    scanned %s (%d chars)", att.name, len(text))
                else:
                    log.info("    skipped %s (unsupported or empty)", att.name)
            except Exception as e:
                log.warning("    attachment failed %s: %s", att.name, e)

    combined = "\n\n".join(text_blobs)
    clauses = extract_clauses(combined)
    standards = extract_standards(combined)

    return {
        "id": op.solicitation or notice.solicitation_number or notice.notice_id or op.title,
        "title": notice.title or op.title,
        "agency": notice.agency or op.agency,
        "naics": notice.naics,
        "psc": notice.classification_code or op.fsc,
        "set_aside": notice.set_aside,
        "posted": notice.posted_date,
        "deadline": notice.response_deadline,
        "url": notice.ui_link,
        "sources": sources,
        "clauses": clauses_to_dicts(clauses),
        "standards": standards_to_dicts(standards),
        "notes": "DEMO MOCK — set SAM_API_KEY for live data" if is_mock else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("email", nargs="?", default=None,
                    help="Path to a bidmatch email (.txt/.eml/.mbox). Defaults to input.txt in this folder.")
    ap.add_argument("--out", default="output/supplier_scorecard.xlsx", help="Output .xlsx path")
    # Simulation mode is intentionally awkward. Real SAM.gov data is the ONLY
    # supported production path. --dev-only-mock-dir is retained for pipeline
    # testing after code changes and requires --i-understand-this-is-simulated
    # to actually run.
    ap.add_argument("--dev-only-mock-dir", dest="mock_dir",
                    help="[NOT FOR PRODUCTION USE] Read canned SAM.gov JSON responses from this dir. "
                         "Requires --i-understand-this-is-simulated. Every row in the output will be "
                         "watermarked as simulated data.")
    ap.add_argument("--i-understand-this-is-simulated", action="store_true",
                    help="Required alongside --dev-only-mock-dir. Confirms you know the output is fabricated.")
    ap.add_argument("--limit", type=int, default=0, help="Only process the first N opportunities (0 = all)")
    ap.add_argument("--no-attachments", action="store_true", help="Skip downloading and scanning attachments")
    ap.add_argument("--include-untitled", action="store_true", help="Also search opportunities with truncated titles")
    ap.add_argument("--rate", type=float, default=1.5, help="Seconds between SAM.gov requests (default 1.5)")
    ap.add_argument("--lookback-days", type=int, default=365,
                    help="How many days back to search SAM.gov (default 365). Increase if solicitations are older.")
    ap.add_argument("--posted-from", help="Override start of search window (MM/DD/YYYY).")
    ap.add_argument("--posted-to", help="Override end of search window (MM/DD/YYYY).")
    ap.add_argument("--filters", default="filters.json",
                    help="Path to a filters JSON to slim the scorecard (default: filters.json; pass empty string to disable)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    if args.email:
        email_path = Path(args.email)
    else:
        email_path = Path(__file__).parent / "input.txt"
    if not email_path.exists():
        print(f"ERROR: {email_path} not found. Paste your bidmatch email into it, or pass a path.")
        return 2
    text = _email_body_text(email_path)
    ops = parse_email_text(text)
    print(f"Parsed {len(ops)} opportunities from {email_path.name}")

    if args.limit:
        ops = ops[: args.limit]
        print(f"  → processing first {len(ops)} (--limit)")

    # Guard: refuse silent simulation.
    if args.mock_dir and not args.i_understand_this_is_simulated:
        print(
            "\nERROR: --dev-only-mock-dir requires --i-understand-this-is-simulated.\n"
            "       Mock output is fabricated data for pipeline testing only —\n"
            "       it does NOT reflect real SAM.gov content. If you want a\n"
            "       supplier scorecard you can act on, set SAM_API_KEY and\n"
            "       run without --dev-only-mock-dir.\n",
            file=sys.stderr,
        )
        return 2

    if args.mock_dir:
        _banner_stderr(
            "SIMULATION MODE",
            "Every row in the resulting xlsx is FABRICATED per FSC×agency template.",
            "This is for testing the pipeline shape ONLY. Do not use it to make",
            "bid/no-bid decisions or to build a real supplier compliance profile.",
            f"Reading canned responses from: {args.mock_dir}",
        )
    else:
        # Real-run guard: if there's no API key, stop with a clear message
        # BEFORE we run through 60 opportunities and hit a wall of errors.
        if not os.environ.get("SAM_API_KEY"):
            print(
                "\nERROR: SAM_API_KEY is not set in the environment.\n\n"
                "This pipeline only produces real supplier scorecards from live\n"
                "SAM.gov data. Get a free key at:\n"
                "  https://open.gsa.gov/api/get-opportunities-public-api/\n\n"
                "Then:  export SAM_API_KEY=your_key   &&   python3 run.py\n",
                file=sys.stderr,
            )
            return 2

    sam = SamClient(
        mock_dir=Path(args.mock_dir) if args.mock_dir else None,
        rate_limit_seconds=args.rate,
        posted_from=args.posted_from,
        posted_to=args.posted_to,
        lookback_days=args.lookback_days,
    )
    if not args.mock_dir:
        print(f"SAM.gov search window: {sam.posted_from} → {sam.posted_to}")

    attach_dir = Path(args.out).parent / "attachments"
    contracts: list[dict] = []
    seen_notice_ids: set[str] = set()

    for i, op in enumerate(ops, 1):
        key = op.search_key()
        prefix = f"[{i:>2}/{len(ops)}] "

        if not _should_search(op) and not args.include_untitled:
            print(f"{prefix}skipped (untitled/truncated): {op.raw[:90]}")
            continue

        query = op.solicitation or op.title
        print(f"{prefix}searching sam.gov: {query}")

        try:
            record = sam.search_solicitation(op.solicitation) if op.solicitation \
                else next(iter(sam.search(op.title or "", limit=1, by="title")), None)
        except SamClientError as e:
            print(f"    ERROR: {e}")
            continue

        if not record:
            print("    no match on SAM.gov")
            continue

        notice = sam.to_notice(record)
        if notice.notice_id in seen_notice_ids:
            print(f"    already processed notice {notice.notice_id}")
            continue
        seen_notice_ids.add(notice.notice_id)

        result = _process_notice(
            op, notice, sam, attach_dir,
            scan_attachments=not args.no_attachments,
            is_mock=bool(args.mock_dir),
        )
        n_clauses = len(result["clauses"])
        print(f"    matched: {notice.solicitation_number or notice.notice_id} — {n_clauses} clauses")
        contracts.append(result)

    print(f"\nAggregating scorecard over {len(contracts)} contract(s)...")
    filters_path = Path(args.filters) if args.filters else None
    if filters_path and not filters_path.is_absolute():
        filters_path = Path(__file__).parent / filters_path
    if filters_path and filters_path.exists():
        print(f"Applying filters from {filters_path.name}")
    out_path = write_scorecard({"contracts": contracts}, Path(args.out), filters_path=filters_path)
    print(f"Wrote {out_path}")

    # also stash raw JSON for auditing
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps({"contracts": contracts}, indent=2), encoding="utf-8")
    print(f"Wrote {json_path}")

    if not contracts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
