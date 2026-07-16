"""Rolling weekly workbook state: INIT/UPDATE modes with fingerprint-based reprice.

The autorun keeps ONE workbook per calendar week (Monday-anchored), backed by
a JSON sidecar state file. On the first run of the week (or if no state file
exists yet) we INIT: walk `acfg.days`-worth-or-more (10 days) of the portal and
price every article found. On subsequent runs (Tue-Sun) we UPDATE: walk a
short window (`acfg.days`), add any newly seen opportunities, and re-price
every still-open row already in state (comparing a fingerprint of the priced
row's fields to detect real changes). Rows whose due date has passed are
frozen — left untouched and not re-priced.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from bidmatch import pipeline
from bidmatch.config import AutorunConfig
from bidmatch.daily_page import OpportunityStub
from bidmatch.excel_writer import OpportunityRow
from bidmatch.pipeline import CollectedArticle


def week_monday(today: date) -> date:
    """Monday of the week containing `today`."""
    return today - timedelta(days=today.weekday())


def state_path(output_dir: Path, monday: date) -> Path:
    return Path(output_dir) / f"bidmatch_week_{monday.isoformat()}.state.json"


def xlsx_path(output_dir: Path, monday: date) -> Path:
    return Path(output_dir) / f"bidmatch_week_{monday.isoformat()}.xlsx"


def row_fingerprint(row: OpportunityRow) -> str:
    """Hash the decision-relevant fields of a priced row.

    Used to detect whether a reprice actually changed anything worth
    reporting, independent of incidental fields like last_updated.

    Beyond the pricing fields, this also covers `set_aside` (partly
    sourced from volatile SAM metadata), `approved_source`, and the
    resulting `decision`: all of these drive the Bid/No Bid/Investigate
    outcome. Hashing pricing alone would let a row whose decision
    flipped without a price change look "unchanged", silently dropping
    the flip.
    """
    payload = json.dumps(
        [
            row.price_source,
            row.est_unit_price,
            row.est_total_value,
            row.n_observations,
            row.price_range,
            row.latest_award_date,
            row.set_aside,
            row.approved_source,
            row.decision,
        ],
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_state(path: Path) -> Optional[dict]:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def _dedup_key(row: OpportunityRow) -> str:
    if row.solicitation_number and row.nsn:
        return f"sn:{row.solicitation_number}|nsn:{row.nsn}"
    if row.solicitation_number:
        return f"sn:{row.solicitation_number}"
    return f"url:{row.article_url}"


def _row_to_dict(row: OpportunityRow) -> dict:
    d = asdict(row)
    d["date"] = d["date"].isoformat()
    return d


def _row_from_dict(d: dict) -> OpportunityRow:
    return OpportunityRow(**{**d, "date": date.fromisoformat(d["date"])})


def _entry_to_state(col: CollectedArticle, row: OpportunityRow, fp: str,
                     added: str, last_updated: str) -> dict:
    return {
        "row": _row_to_dict(row),
        "fields": col.fields,
        "stub": asdict(col.stub),
        "entry_date": col.entry_date.isoformat(),
        "article_url": col.article_url,
        "fingerprint": fp,
        "added": added,
        "last_updated": last_updated,
    }


def _collected_from_state(entry: dict) -> CollectedArticle:
    stub = OpportunityStub(**entry["stub"])
    return CollectedArticle(
        stub=stub,
        entry_date=date.fromisoformat(entry["entry_date"]),
        fields=entry["fields"],
        article_url=entry["article_url"],
    )


def run_week(
    token: str,
    acfg: AutorunConfig,
    cache,
    dibbs_session,
    skip_sam: bool,
    sam_api_key: str,
    progress: Callable[[str], None] = lambda _: None,
) -> Tuple[List[OpportunityRow], Dict[str, int]]:
    today = date.today()
    today_iso = today.isoformat()
    monday = week_monday(today)
    sp = state_path(acfg.output_dir, monday)
    state = load_state(sp)
    mode = "INIT" if state is None else "UPDATE"

    delta = {"new": 0, "repriced": 0, "unchanged": 0, "frozen": 0}
    pulled_at = today_iso

    if mode == "INIT":
        collected = pipeline.collect(token, 10, progress=progress)
        rows_state: Dict[str, dict] = {}
        rows_out: List[OpportunityRow] = []
        seen: set = set()
        for col in collected:
            row = pipeline.price_and_build(
                col, cache, dibbs_session, skip_sam, sam_api_key, pulled_at,
                cp_set_asides=acfg.cp_set_asides,
            )
            key = _dedup_key(row)
            if key in seen:
                continue
            seen.add(key)
            row = replace(row, last_updated=today_iso)
            fp = row_fingerprint(row)
            rows_state[key] = _entry_to_state(col, row, fp, added=today_iso, last_updated=today_iso)
            rows_out.append(row)
            delta["new"] += 1
        state = {"week_of": monday.isoformat(), "rows": rows_state}
        save_state(sp, state)
        return rows_out, delta

    # UPDATE mode
    rows_state = dict(state.get("rows", {}))
    existing_keys = set(rows_state.keys())

    collected = pipeline.collect(token, acfg.days, progress=progress)
    seen_new: set = set()
    for col in collected:
        row = pipeline.price_and_build(
            col, cache, dibbs_session, skip_sam, sam_api_key, pulled_at,
            cp_set_asides=acfg.cp_set_asides,
        )
        key = _dedup_key(row)
        if key in existing_keys or key in seen_new:
            continue
        seen_new.add(key)
        row = replace(row, last_updated=today_iso)
        fp = row_fingerprint(row)
        rows_state[key] = _entry_to_state(col, row, fp, added=today_iso, last_updated=today_iso)
        delta["new"] += 1

    rows_out: List[OpportunityRow] = []
    for key in list(rows_state.keys()):
        entry = rows_state[key]
        row = _row_from_dict(entry["row"])

        if key in seen_new:
            # Just added above this run — already priced, nothing more to do.
            rows_out.append(row)
            continue

        if row.due_date and row.due_date < today_iso:
            delta["frozen"] += 1
            rows_out.append(row)
            continue

        col = _collected_from_state(entry)
        new_row = pipeline.price_and_build(
            col, cache, dibbs_session, skip_sam, sam_api_key, pulled_at,
            cp_set_asides=acfg.cp_set_asides,
        )
        new_fp = row_fingerprint(new_row)
        if new_fp != entry["fingerprint"]:
            new_row = replace(new_row, last_updated=today_iso)
            rows_state[key] = _entry_to_state(
                col, new_row, new_fp, added=entry["added"], last_updated=today_iso,
            )
            rows_out.append(new_row)
            delta["repriced"] += 1
        else:
            delta["unchanged"] += 1
            rows_out.append(row)

    state = {"week_of": state.get("week_of", monday.isoformat()), "rows": rows_state}
    save_state(sp, state)
    return rows_out, delta
