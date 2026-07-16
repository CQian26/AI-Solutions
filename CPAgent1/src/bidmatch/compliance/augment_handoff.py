"""Augment an Agent 2 Handoff CSV with a Requirements CSV column.

Reads a CSV in the same shape the BidMatch pipeline emits (e.g.
`bidmatch_v2_3dAgent_2_Handoff.csv`), runs the DIBBS compliance
extractor for every row whose `Solicitation #` looks like a DLA sol,
and writes a new CSV with an extra `Requirements CSV` column pointing
at the per-solicitation `<SOL>_requirements.csv` produced on disk.

Rows without a solicitation number, or whose sol is non-DIBBS (state
portals, other agencies), are left untouched with a short reason.

Usage:
  python -m bidmatch.compliance.augment_handoff INPUT.csv [--outdir DIR] \\
      [--output OUTPUT.csv] [--limit N] [--force]

Flags:
  --outdir     Where per-sol compliance files go (default ./compliance)
  --output     Augmented CSV path (default: INPUT with '_augmented' suffix)
  --limit N    Only process the first N rows (for smoke tests)
  --force      Re-run the extractor even if the CSV already exists
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from bidmatch.compliance.runner import extract_for_solicitation


def _find_sol_column(headers: list[str]) -> int:
    """Locate the Solicitation column. The sample handoff exports use
    'Solicitation #' as the visible header; be tolerant of casing/space
    variants so hand-edited CSVs still work."""
    normalized = [h.strip().lower() for h in headers]
    for candidate in ("solicitation #", "solicitation#", "solicitation",
                      "solicitation_number"):
        if candidate in normalized:
            return normalized.index(candidate)
    raise KeyError("No Solicitation column found in headers: " + ", ".join(headers))


def augment_csv(
    input_csv: Path,
    outdir: Path,
    output_csv: Path,
    *,
    limit: int | None = None,
    force: bool = False,
) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    input_csv = Path(input_csv)

    with open(input_csv, newline="") as f:
        reader = csv.reader(f)
        all_rows = list(reader)

    # The sample handoff exports carry two leading title rows before the
    # header row; scan for the first row that contains a Solicitation
    # column and treat that as the header. Everything before is preserved
    # verbatim on write.
    header_idx = None
    for i, row in enumerate(all_rows[:5]):
        normalized = [c.strip().lower() for c in row]
        if any(c in {"solicitation #", "solicitation#", "solicitation",
                     "solicitation_number"} for c in normalized):
            header_idx = i
            break
    if header_idx is None:
        raise KeyError(f"Could not locate the header row in {input_csv}")

    preamble = all_rows[:header_idx]
    header = all_rows[header_idx]
    body = all_rows[header_idx + 1:]

    sol_col = _find_sol_column(header)

    new_header = header + ["Requirements CSV", "Compliance Status", "Clause Count"]

    output_body: list[list[str]] = []
    stats = {"ok": 0, "cached": 0, "skipped": 0, "no_pdf": 0,
             "no_text": 0, "error": 0, "blank": 0, "total": 0}

    for i, row in enumerate(body):
        if limit is not None and i >= limit:
            output_body.append(row + ["", "not processed (limit)", ""])
            continue
        # Pad short rows so column access is safe
        row = list(row) + [""] * max(0, len(header) - len(row))
        sol = (row[sol_col] or "").strip()
        stats["total"] += 1
        if not sol:
            stats["blank"] += 1
            output_body.append(row + ["", "no solicitation number", ""])
            continue

        result = extract_for_solicitation(sol, outdir, force=force)
        stats[result.status] = stats.get(result.status, 0) + 1

        csv_ref = str(result.requirements_csv) if result.requirements_csv else ""
        note = ""
        if result.status in ("ok", "cached"):
            note = result.status
        else:
            note = f"{result.status}: {result.error}" if result.error else result.status

        output_body.append(
            row + [csv_ref, note, str(result.clause_count or "")]
        )
        print(f"[{i + 1}/{len(body)}] {sol}: {note} "
              f"({result.clause_count} clauses)")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", newline="") as f:
        w = csv.writer(f)
        for row in preamble:
            w.writerow(row)
        w.writerow(new_header)
        for row in output_body:
            w.writerow(row)

    return {"output": str(output_csv), **stats}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", type=Path,
                    help="Agent 2 Handoff CSV to augment")
    ap.add_argument("--outdir", type=Path, default=Path("./compliance"),
                    help="Where per-sol compliance files go (default ./compliance)")
    ap.add_argument("--output", type=Path, default=None,
                    help="Augmented CSV path (default: INPUT with '_augmented' suffix)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the first N rows (for smoke tests)")
    ap.add_argument("--force", action="store_true",
                    help="Re-run extractor even if the CSV already exists")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    output_csv = args.output or args.input.with_name(
        args.input.stem + "_augmented" + args.input.suffix
    )
    stats = augment_csv(args.input, args.outdir, output_csv,
                        limit=args.limit, force=args.force)

    print("\n" + "=" * 60)
    print(f"Wrote {stats['output']}")
    print("=" * 60)
    for k in ("total", "ok", "cached", "skipped", "no_pdf",
              "no_text", "error", "blank"):
        if k in stats:
            print(f"  {k:>10}: {stats[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
