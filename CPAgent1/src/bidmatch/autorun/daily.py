"""Daily autorun entry point: python -m bidmatch.autorun.daily

Task Scheduler (or EventBridge later) fires this at 8:00am. It maintains
the current week's workbook: INIT (10-day walk) on Mondays or first run,
incremental UPDATE otherwise. Success and failure are both emailed.
"""

import logging
import sys
import traceback
from datetime import date

import requests

from bidmatch.autorun.notify import send_success, send_failure
from bidmatch.autorun.weekly import run_week, week_monday, xlsx_path
from bidmatch.cache import Cache
from bidmatch.config import load_config, load_autorun_config, ConfigError
from bidmatch.excel_writer import write_workbook

log = logging.getLogger("bidmatch")
LOG_FILE = "autorun.log"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    try:
        acfg = load_autorun_config()
    except ConfigError as exc:
        log.error(str(exc))
        return 1

    stage = "logging-setup"
    try:
        acfg.output_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(acfg.output_dir / LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(file_handler)

        stage = "config"
        cfg = load_config()
        stage = "pipeline"
        cache = Cache(cfg.cache_dir)
        session = requests.Session()
        session.headers["User-Agent"] = "BidMatch-Extractor/0.1 (+contact: cp-industries)"
        rows, delta = run_week(
            token=cfg.token, acfg=acfg, cache=cache, dibbs_session=session,
            skip_sam=not cfg.sam_api_key, sam_api_key=cfg.sam_api_key,
            progress=lambda line: log.info(line),
        )
        stage = "workbook"
        out = xlsx_path(acfg.output_dir, week_monday(date.today()))
        write_workbook(out, rows)
        stage = "notify"
        summary = {
            "bid": sum(1 for r in rows if r.decision == "Bid"),
            "no_bid": sum(1 for r in rows if r.decision == "No Bid"),
            "investigate": sum(1 for r in rows if r.decision not in ("Bid", "No Bid")),
            "high_pipeline": sum(
                (r.est_total_value or 0.0) for r in rows
                if r.decision == "Bid" and r.value_confidence == "High"
            ),
        }
        send_success(acfg, out, delta, summary)
        log.info("autorun complete: %s | delta %s", out.name, delta)
        return 0
    except Exception:  # noqa: BLE001 — top-level: report, never raise
        err = traceback.format_exc()
        log.error("autorun FAILED at %s:\n%s", stage, err)
        try:
            send_failure(acfg, stage, err)
        except Exception:  # noqa: BLE001 — SMTP down: log already has it
            log.error("failure email could not be sent")
        return 1


if __name__ == "__main__":
    sys.exit(main())
