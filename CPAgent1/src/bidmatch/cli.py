"""CLI: thin wrapper around pipeline.execute()."""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import List

from bidmatch.config import load_config, ConfigError
from bidmatch.pipeline import execute

log = logging.getLogger("bidmatch")


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bidmatch",
        description="BidMatch extractor: walk portal, price via DIBBS Section A, triage, write 5-sheet workbook.",
    )
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument(
        "--skip-pricing", action="store_true",
        help="Skip all pricing/SAM/DIBBS calls; useful for offline parser smoke",
    )
    p.add_argument("--skip-sam", action="store_true")
    p.add_argument(
        "--skip-compliance", action="store_true",
        help="Skip the DIBBS compliance extractor (no requirements CSVs)",
    )
    p.add_argument(
        "--compliance-dir", type=Path, default=None,
        help="Where to write per-solicitation compliance CSVs "
             "(default: <output-parent>/compliance)",
    )
    return p.parse_args(argv)


def run(argv: List[str]) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    try:
        cfg = load_config()
    except ConfigError as exc:
        log.error(str(exc))
        return 2

    days = args.days if args.days is not None else cfg.days
    output = args.output or cfg.output or Path(
        f"bidmatch_{date.today().isoformat()}.xlsx"
    )

    execute(
        token=cfg.token,
        days=days,
        output=output,
        no_cache=args.no_cache,
        skip_pricing=args.skip_pricing,
        skip_sam=args.skip_sam,
        sam_api_key=cfg.sam_api_key,
        cache_dir=cfg.cache_dir,
        skip_compliance=args.skip_compliance,
        compliance_dir=args.compliance_dir,
    )
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
