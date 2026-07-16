"""Pipeline orchestration: walks portal, parses, prices, triages, writes xlsx.

Extracted so both the CLI and the local web UI call the same code path.
Reports progress via a callback so the UI can stream log lines.
"""

import dataclasses
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List

import requests

from bidmatch.article_page import parse_article
from bidmatch.cache import Cache
from bidmatch.compliance import extract_for_solicitation
from bidmatch.daily_page import parse_daily, OpportunityStub
from bidmatch.decision import decide, detect_approved_source
from bidmatch.enrich.flis import lookup as flis_lookup
from bidmatch.excel_writer import write_workbook, OpportunityRow
from bidmatch.http_client import BidMatchClient
from bidmatch.index_page import parse_index, IndexEntry
from bidmatch.parsers.amsc import decode as decode_amsc
from bidmatch.pricing import dibbs_awards, dibbs_award_pdf, dibbs_section_a, usaspending, sam
from bidmatch.pricing.aggregate import aggregate, PriceResult
from bidmatch.pricing.router import route
from bidmatch.triage import compute as triage_compute

log = logging.getLogger("bidmatch")

IMMUTABLE_TTL = 10 * 365 * 86400.0   # award PDFs, Section A of a given sol
VOLATILE_TTL = 20 * 3600.0           # award pages, USA/SAM queries — fresh daily


@dataclass(frozen=True)
class CollectedArticle:
    stub: OpportunityStub
    entry_date: date
    fields: Dict
    article_url: str


def _price_opportunity(
    fields: Dict,
    cache: Cache,
    dibbs_session: requests.Session | None,
    skip_sam: bool,
    sam_api_key: str,
) -> tuple[PriceResult, Dict[str, str]]:
    nsn = fields.get("nsn", "")
    sol = fields.get("solicitation_number", "")
    parsed_psc = fields.get("psc", "")
    route_name = route(sol, nsn)
    nsn_digits = "".join(c for c in nsn if c.isdigit())
    psc = parsed_psc or (nsn_digits[:4] if len(nsn_digits) >= 4 else "")
    dla = route_name == "dla"

    # Tier 1 — Section A of the current RFQ PDF (immutable per sol)
    section_a: list = []
    if dla and sol:
        cached = cache.get("dibbs_section_a", sol, ttl=IMMUTABLE_TTL)
        if cached is not None:
            section_a = cached
        elif dibbs_session is not None:
            section_a = dibbs_section_a.fetch_section_a_awards(sol, dibbs_session)
            cache.set("dibbs_section_a", sol, section_a)

    # Tier 3 source data — Awards page (volatile; also feeds tier 2)
    totals: list = []
    if dla and nsn_digits and not section_a:
        cached = cache.get("dibbs_totals", nsn_digits, ttl=VOLATILE_TTL)
        if cached is not None:
            totals = cached
        elif dibbs_session is not None:
            totals = dibbs_awards.fetch_awards(nsn, dibbs_session)
            cache.set("dibbs_totals", nsn_digits, totals)

    # Tier 2 — award-document PDFs of the most recent per-buy awards
    award_rows: list = []
    if dla and totals and not section_a:
        cached = cache.get("award_pdfs", nsn_digits, ttl=IMMUTABLE_TTL)
        if cached is not None:
            award_rows = cached
        elif dibbs_session is not None:
            award_rows = dibbs_award_pdf.fetch_award_unit_prices(
                totals, dibbs_session, max_pdfs=3
            )
            cache.set("award_pdfs", nsn_digits, award_rows)

    # Tier 4 — USASpending NSN fuzzy (volatile)
    usa_nsn: list = []
    if nsn_digits:
        cached = cache.get("usa_nsn", nsn_digits, ttl=VOLATILE_TTL)
        if cached is not None:
            usa_nsn = cached
        else:
            usa_nsn = usaspending.query_by_nsn_text(nsn_digits)
            cache.set("usa_nsn", nsn_digits, usa_nsn)

    # Tier 5 — SAM award notices (volatile; gated on key)
    sam_awards: list = []
    if not skip_sam and sam_api_key and nsn_digits:
        cached = cache.get("sam_awards", nsn_digits, ttl=VOLATILE_TTL)
        if cached is not None:
            sam_awards = cached
        else:
            sam_awards = sam.search_award_amounts(nsn_digits, sam_api_key)
            cache.set("sam_awards", nsn_digits, sam_awards)

    # Tier 6 — USASpending PSC (volatile)
    nomenclature = (fields.get("nomenclature") or "").strip()
    keyword = nomenclature.split(",")[0].strip() if nomenclature else ""
    usa_psc: list = []
    if psc:
        cache_key = f"{psc}_{keyword}" if keyword else psc
        cached = cache.get("usa_psc", cache_key, ttl=VOLATILE_TTL)
        if cached is not None:
            usa_psc = cached
        else:
            usa_psc = usaspending.query_by_psc(psc, keyword)
            cache.set("usa_psc", cache_key, usa_psc)

    # SAM metadata (volatile) — unchanged behavior
    sam_meta: Dict[str, str] = {}
    if not skip_sam and sol:
        cached = cache.get("sam", sol, ttl=VOLATILE_TTL)
        if cached is not None:
            sam_meta = cached
        else:
            sam_meta = sam.fetch_metadata(sol, sam_api_key)
            cache.set("sam", sol, sam_meta)

    query_summary = f"route={route_name} nsn={nsn or 'none'} psc={psc or 'none'}"
    result = aggregate(
        dibbs_section_a_awards=section_a,
        award_pdf_rows=award_rows,
        dibbs_award_totals=totals,
        usa_nsn_matches=usa_nsn,
        sam_award_rows=sam_awards,
        usa_psc_matches=usa_psc,
        query_summary=query_summary,
    )
    return result, sam_meta


def _build_row(
    stub: OpportunityStub,
    entry_date: date,
    fields: Dict,
    article_url: str,
    flis: Dict[str, str],
    amsc: Dict[str, str],
    price: PriceResult,
    sam_meta: Dict[str, str],
    pulled_at: str,
    cp_set_asides: List[str] | None = None,
) -> OpportunityRow:
    cp_set_asides = cp_set_asides or ["small_business"]
    triage = triage_compute(
        fields.get("qty_value"),
        price.est_unit_price,
        total_magnitude_estimate=price.total_magnitude_estimate,
    )
    price_range = (
        f"{price.price_range[0]:.2f} - {price.price_range[1]:.2f}"
        if price.price_range else ""
    )
    parsed_sa = (fields.get("set_aside") or "").strip()
    sam_sa = (sam_meta.get("set_aside") or "").strip()
    if parsed_sa:
        set_aside = parsed_sa
    elif sam_sa:
        set_aside = sam_sa
    elif sam_meta:   # SAM answered for this sol and reported no set-aside
        set_aside = "No set-asides confirmed"
    else:            # genuinely unknown
        set_aside = ""
    approved_source_value = detect_approved_source(
        amsc["amsc"], amsc["amsc_meaning"], amsc["amsc_risk"],
        fields.get("description", ""),
    )
    decision, decision_reason = decide(
        triage.est_total_value, price.value_confidence, set_aside,
        approved_source_value, triage.ceiling_flag, cp_set_asides,
    )
    return OpportunityRow(
        date=entry_date,
        source_code=stub.source_code,
        agency=stub.agency,
        fsc_group=stub.fsc_group,
        naics=fields.get("naics", ""),
        nomenclature=fields.get("nomenclature") or stub.title,
        qty_value=fields.get("qty_value"),
        qty_unit=fields.get("qty_unit", ""),
        nsn=fields.get("nsn", ""),
        set_aside=set_aside,
        amsc=amsc["amsc"],
        amsc_meaning=amsc["amsc_meaning"],
        amsc_risk=amsc["amsc_risk"],
        solicitation_number=fields.get("solicitation_number", ""),
        pr_number=fields.get("pr_number", ""),
        due_date=fields.get("due_date", "") or sam_meta.get("response_deadline", "")[:10],
        apex_contact=fields.get("apex_contact", ""),
        outreach_article_number=fields.get("outreach_article_number", "") or f"{stub.doc}#{stub.seq}",
        material=flis["material"],
        dimensions=flis["dimensions"],
        surface_treatment=flis["surface_treatment"],
        criticality=flis["criticality"],
        hazmat_indicator=flis["hazmat_indicator"],
        precious_metals_indicator=flis["precious_metals_indicator"],
        demil_code=flis["demil_code"],
        export_classification=flis["export_classification"],
        approved_supplier_present=flis["approved_supplier_present"],
        demand_forecast_value=flis["demand_forecast_value"],
        flis_source=flis["flis_source"],
        est_unit_price=price.est_unit_price,
        n_observations=price.n_observations,
        price_range=price_range,
        latest_award_date=price.latest_award_date,
        price_source=price.price_source,
        value_confidence=price.value_confidence,
        value_basis=price.value_basis,
        est_total_value=triage.est_total_value,
        over_20k=triage.over_20k,
        value_band=triage.value_band,
        ceiling_flag=triage.ceiling_flag,
        article_url=article_url,
        rfq_pdf_url=fields.get("rfq_pdf_url", ""),
        package_view_url=fields.get("package_view_url", ""),
        description=fields.get("description", ""),
        pulled_at=pulled_at,
        approved_source=approved_source_value,
        price_query=price.price_query,
        decision=decision,
        decision_reason=decision_reason,
    )


def collect(
    token: str,
    days: int,
    *,
    progress: Callable[[str], None] = lambda _: None,
    no_cache: bool = False,
    cache_dir: Path = Path(".cache"),
) -> List[CollectedArticle]:
    def _say(line: str) -> None:
        log.info(line)
        progress(line)

    client = BidMatchClient(token=token)
    _say("Fetching index...")
    entries: List[IndexEntry] = parse_index(client.get(client.index_url()))
    cutoff = date.today() - timedelta(days=days)
    entries = [e for e in entries if e.date >= cutoff and e.article_count > 0]
    _say(f"Walking {len(entries)} daily listings (cutoff {cutoff})")
    out: List[CollectedArticle] = []
    for entry in entries:
        _say(f"Daily {entry.date} ({entry.article_count} articles)")
        stubs = parse_daily(client.get(client.daily_url(entry.doc)), doc=entry.doc)
        for stub in stubs:
            url = client.article_url(stub.doc, stub.seq)
            try:
                html = client.get(url)
            except Exception as exc:  # noqa: BLE001
                log.warning("article fetch failed seq=%s: %s", stub.seq, exc)
                continue
            fields = parse_article(html, source_code=stub.source_code)
            out.append(CollectedArticle(stub, entry.date, fields, url))
    return out


def price_and_build(
    col: CollectedArticle,
    cache: Cache,
    dibbs_session: requests.Session | None,
    skip_sam: bool,
    sam_api_key: str,
    pulled_at: str,
    skip_pricing: bool = False,
    cp_set_asides: List[str] | None = None,
) -> OpportunityRow:
    cp_set_asides = cp_set_asides or ["small_business"]
    amsc = decode_amsc(col.fields.get("amsc", ""))
    flis = flis_lookup(col.fields.get("nsn", ""))
    if skip_pricing:
        price = PriceResult(
            est_unit_price=None, n_observations=0, price_range=None,
            latest_award_date="", price_source="", value_confidence="None",
            value_basis="skipped via skip_pricing", price_query="",
        )
        sam_meta: Dict[str, str] = {}
    else:
        price, sam_meta = _price_opportunity(
            col.fields, cache, dibbs_session, skip_sam, sam_api_key,
        )
    return _build_row(col.stub, col.entry_date, col.fields, col.article_url,
                      flis, amsc, price, sam_meta, pulled_at, cp_set_asides)


def _attach_compliance(
    row: OpportunityRow,
    outdir: Path,
    say: Callable[[str], None],
) -> OpportunityRow:
    """Run the DIBBS compliance extractor for one row and return a new
    OpportunityRow with the requirements CSV fields populated.

    Non-DIBBS rows and extractor failures are recorded on the row (as
    `requirements_status`) but never stop the pipeline.
    """
    sol = row.solicitation_number or ""
    result = extract_for_solicitation(sol, outdir)
    csv_path = ""
    if result.requirements_csv is not None:
        csv_path = str(result.requirements_csv)
    if result.status == "ok":
        say(f"  compliance {sol}: {result.clause_count} clauses → {csv_path}")
    elif result.status == "cached":
        say(f"  compliance {sol}: cached ({result.clause_count} clauses)")
    elif result.status == "skipped":
        pass  # not DIBBS-shaped; silent, most rows will hit this
    else:
        say(f"  compliance {sol}: {result.status} ({result.error or 'no detail'})")
    return dataclasses.replace(
        row,
        requirements_csv=csv_path,
        requirements_status=result.status,
        requirements_clause_count=result.clause_count,
    )


def execute(
    token: str,
    days: int,
    output: Path,
    *,
    progress: Callable[[str], None] = lambda _: None,
    no_cache: bool = False,
    skip_pricing: bool = False,
    skip_sam: bool = False,
    sam_api_key: str = "",
    cache_dir: Path = Path(".cache"),
    cp_set_asides: List[str] | None = None,
    skip_compliance: bool = False,
    compliance_dir: Path | None = None,
) -> dict:
    """Run the full pipeline.

    `progress(line)` is invoked for each log line so callers can stream
    progress (e.g. to a UI). Returns a summary dict.
    """
    cp_set_asides = cp_set_asides or ["small_business"]

    def _say(line: str) -> None:
        log.info(line)
        progress(line)

    cache = Cache(cache_dir, enabled=not no_cache)
    dibbs_session = None if skip_pricing else requests.Session()
    if dibbs_session is not None:
        dibbs_session.headers["User-Agent"] = "BidMatch-Extractor/0.1 (+contact: cp-industries)"

    pulled_at = datetime.now().astimezone().isoformat(timespec="seconds")

    collected = collect(token, days, progress=progress, no_cache=no_cache, cache_dir=cache_dir)

    rows: List[OpportunityRow] = []
    seen: set[str] = set()

    for col in collected:
        row = price_and_build(col, cache, dibbs_session, skip_sam, sam_api_key, pulled_at,
                               skip_pricing, cp_set_asides)
        key = (
            f"sn:{row.solicitation_number}|nsn:{row.nsn}"
            if row.solicitation_number and row.nsn
            else f"sn:{row.solicitation_number}" if row.solicitation_number
            else f"url:{row.article_url}"
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    if not skip_compliance:
        compliance_out = compliance_dir or Path(output).parent / "compliance"
        compliance_out.mkdir(parents=True, exist_ok=True)
        _say(f"Compliance extraction → {compliance_out}")
        rows = [_attach_compliance(r, compliance_out, _say) for r in rows]

    written = write_workbook(output, rows)

    over = [r for r in rows if r.over_20k == "Yes"]
    under = [r for r in rows if r.over_20k == "No"]
    needs = [r for r in rows if r.over_20k == "Unknown"]
    high_over = [r for r in over if r.value_confidence == "High"]
    high_pipeline = sum((r.est_total_value or 0.0) for r in high_over)

    summary = {
        "total": written,
        "over_20k": len(over),
        "under_20k": len(under),
        "needs_pricing": len(needs),
        "high_count": len(high_over),
        "high_pipeline": high_pipeline,
        "output": str(Path(output).resolve()),
    }
    _say(
        f"{summary['total']} opportunities | Over 20k: {summary['over_20k']} "
        f"(High: {summary['high_count']}) | Under 20k: {summary['under_20k']} | "
        f"Needs Pricing: {summary['needs_pricing']} | "
        f"High-tier pipeline: ${summary['high_pipeline']:,.2f}"
    )
    return summary
