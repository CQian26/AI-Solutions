#!/usr/bin/env python3
"""
dibbs_compliance_extractor  (browser version)

Uses a headless Chromium browser (via Playwright) to fetch DLA DIBBS
solicitations and extract every compliance clause. Handles the DoD
consent banner automatically the same way a real browser does.

One-time setup:
  pip install playwright pypdf
  playwright install chromium

Callable both as a module (`extract(sol_num, outdir)`) and as a CLI, so
the BidMatch pipeline can invoke it per row and the original standalone
usage still works:

  python -m bidmatch.compliance.dibbs_extractor SPE7L426T5346
  python -m bidmatch.compliance.dibbs_extractor SPE7L426T5346 --outdir ./results
  python -m bidmatch.compliance.dibbs_extractor SPE7L426T5346 --headed
  python -m bidmatch.compliance.dibbs_extractor SPE7L426T5346 --pdf ./local.pdf

Outputs (in --outdir, default ./dibbs_output):
  - <sol>_metadata.json      : scraped listing metadata
  - <sol>_clauses.json       : clauses found in the RFQ PDF
  - <sol>_requirements.csv   : flat requirements list
  - <sol>_docs/              : the downloaded PDF

The DLA Master Solicitation is incorporated by reference in nearly every
RFQ — standard clauses (Section 889, most DFARS flowdowns, MIL-STD marking,
cyber/CUI clauses at applicable thresholds) apply even when the RFQ PDF
doesn't list them. The output includes a reminder.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# pypdf is imported lazily inside extract_text() so this module can be
# imported (and the CLI --help can run) on a machine that has not yet
# installed it. The BidMatch pipeline imports this module unconditionally
# and must not blow up if the optional PDF dep is missing.


# -----------------------------------------------------------------------------
# Clause patterns
# -----------------------------------------------------------------------------

CLAUSE_PATTERNS = [
    ("FAR",         re.compile(r"\bFAR\s*(52\.\d{3}-\d+)\b", re.I)),
    ("DFARS",       re.compile(r"\bDFARS\s*(252\.\d{3}-\d+)\b", re.I)),
    ("DFARS_PGI",   re.compile(r"\bDFARS PGI\s*(2\d{2}\.\d+)\b", re.I)),
    ("DLAD",        re.compile(r"\bDLAD\s*(52\.\d{3}-\d+)\b", re.I)),
    ("MIL-STD",     re.compile(r"\bMIL[-\s]?STD[-\s]?(\d+[A-Z]?(?:/\d+)?)\b", re.I)),
    ("MIL-PRF",     re.compile(r"\bMIL[-\s]?PRF[-\s]?(\d+[A-Z]?)\b", re.I)),
    ("MIL-DTL",     re.compile(r"\bMIL[-\s]?DTL[-\s]?(\d+[A-Z]?)\b", re.I)),
    ("MIL-SPEC",    re.compile(r"\bMIL[-\s]?([A-Z]{1,3}-\d+[A-Z]?)\b")),
    ("ISO",         re.compile(r"\bISO\s*(9\d{3}(?::\d{4})?)\b")),
    ("AS_QMS",      re.compile(r"\bAS\s*9(100|110|120)[A-Z]?\b", re.I)),
    ("NIST_800",    re.compile(r"\bNIST\s*(?:SP\s*)?800[-\s]?(\d+[A-Z]?)\b", re.I)),
    ("CMMC",        re.compile(r"\bCMMC\s*(?:Level\s*)?(\d)\b", re.I)),
    ("NAICS",       re.compile(r"\bNAICS[:\s]*(\d{6})\b", re.I)),
    ("PSC",         re.compile(r"\bPSC[:\s]*([A-Z0-9]{4})\b")),
    ("FSC",         re.compile(r"\bFSC[:\s]*(\d{4})\b", re.I)),
    ("BERRY",       re.compile(r"\bBerry\s+Amendment\b", re.I)),
    ("SECTION_889", re.compile(r"\bSection\s+889\b", re.I)),
    ("ITAR",        re.compile(r"\bITAR\b|\bInternational Traffic in Arms\b", re.I)),
    ("EAR",         re.compile(r"\bEAR\b|\bExport Administration Regulations\b")),
    ("DPAS",        re.compile(r"\bDPAS\s+(?:rating\s*[:=]?\s*)?([A-Z]{2}\d?)\b", re.I)),
    ("TAA",         re.compile(r"\bTrade Agreements Act\b|\bTAA[- ]compliant\b", re.I)),
    ("BUY_AMERICAN",re.compile(r"\bBuy American\b", re.I)),
    ("DIST_STMT",   re.compile(r"\bDistribution\s+Statement\s+([A-F])\b", re.I)),
    ("CFOLDERS",    re.compile(r"\bcFolders\b", re.I)),
    ("JCP",         re.compile(r"\bJoint Certification Program\b|\bJCP\b")),
    ("QPL",         re.compile(r"\bQualified Products List\b|\bQPL\b")),
    ("QML",         re.compile(r"\bQualified Manufacturers List\b|\bQML\b")),
]

CATEGORY_MAP = {
    "FAR": "Registration & baseline", "DFARS": "DoD-specific",
    "DFARS_PGI": "DoD-specific", "DLAD": "DLA-specific",
    "MIL-STD": "Marking / packaging / quality", "MIL-PRF": "Performance spec",
    "MIL-DTL": "Detail spec", "MIL-SPEC": "Military specification",
    "ISO": "Quality management", "AS_QMS": "Aerospace quality",
    "NIST_800": "Cybersecurity", "CMMC": "Cybersecurity",
    "NAICS": "Classification", "PSC": "Classification", "FSC": "Classification",
    "BERRY": "Domestic sourcing", "SECTION_889": "Supply chain security",
    "ITAR": "Export control", "EAR": "Export control", "DPAS": "Priority rating",
    "TAA": "Trade agreements", "BUY_AMERICAN": "Domestic sourcing",
    "DIST_STMT": "Data distribution", "CFOLDERS": "Technical data access",
    "JCP": "Technical data access", "QPL": "Approved-source restriction",
    "QML": "Approved-source restriction",
}


# -----------------------------------------------------------------------------
# Browser-driven DIBBS fetch
# -----------------------------------------------------------------------------

def fetch_via_browser(sol_num: str, doc_dir: Path, headed: bool = False):
    """
    Drive Chromium to accept the DoD consent, navigate to the RFQ record
    page, scrape metadata, and download the PDF.

    Returns (pdf_path or None, metadata_dict).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright is required. Install with:\n"
            "  pip install playwright\n"
            "  playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    meta: dict = {}
    pdf_path = None

    import os
    launch_kwargs = {"headless": not headed}
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "").strip()
    if exe:
        launch_kwargs["executable_path"] = exe

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        target = f"https://www.dibbs.bsm.dla.mil/rfq/rfqrec.aspx?sn={sol_num}"
        print(f"  Loading {target}")
        page.goto(target, wait_until="domcontentloaded", timeout=45000)

        clicked = False
        for sel in [
            'input[value="OK"]',
            'input[type="submit"][value="OK"]',
            'button:has-text("OK")',
            'a:has-text("OK")',
        ]:
            if page.locator(sel).count() > 0:
                print(f"  Clicking DoD consent OK ({sel})")
                try:
                    page.locator(sel).first.click()
                    page.wait_for_load_state("networkidle", timeout=30000)
                    clicked = True
                    break
                except Exception as e:
                    print(f"  ! Click on {sel} failed: {e}", file=sys.stderr)
        if not clicked:
            print("  (no consent banner detected — proceeding)")

        meta["listing_url"] = page.url
        html = page.content()

        patterns = {
            "nsn":          re.compile(r"([\d]{4}-\d{2}-\d{3}-\d{4})"),
            "nomenclature": re.compile(r"Nomenclature[^<]*<[^>]*>([^<]{5,120})", re.I),
            "quantity":     re.compile(r"Quantity[^<]*<[^>]*>([^<]{1,40})", re.I),
            "return_by":    re.compile(r"Return\s*By[^<]*<[^>]*>([^<]{5,40})", re.I),
            "issue_date":   re.compile(r"Issue\s*Date[^<]*<[^>]*>([^<]{5,40})", re.I),
            "small_biz":    re.compile(r"Small\s*Bus[^<]*<[^>]*>([^<]{1,40})", re.I),
        }
        for key, pat in patterns.items():
            m = pat.search(html)
            if m:
                meta[key] = m.group(1).strip()

        pdf_link = None
        for sel in [
            f'a[href*="{sol_num}.PDF"]',
            f'a[href*="{sol_num}.pdf"]',
            'a[href*="Downloads/RFQ"][href*=".pdf"]',
            'a[href*="Downloads/RFQ"][href*=".PDF"]',
            'a:has(img[src*="pdf"])',
        ]:
            if page.locator(sel).count() > 0:
                pdf_link = page.locator(sel).first
                href = pdf_link.get_attribute("href")
                if href:
                    meta["pdf_url_from_listing"] = (
                        href if href.startswith("http")
                        else f"https://www.dibbs.bsm.dla.mil{href}"
                    )
                    print(f"  Found PDF link: {meta['pdf_url_from_listing']}")
                break

        if not pdf_link:
            print("  ! No PDF link found on record page. Snapshot saved to "
                  f"{doc_dir}/page_snapshot.html for debugging.")
            (doc_dir / "page_snapshot.html").write_text(html)
            browser.close()
            return None, meta

        pdf_url = meta.get("pdf_url_from_listing")
        if pdf_url:
            print(f"  Fetching PDF: {pdf_url}")
            download_holder: dict = {}

            def _on_download(dl):
                download_holder["dl"] = dl

            page.on("download", _on_download)

            import time
            for attempt in range(1, 4):
                if "dl" in download_holder:
                    break
                try:
                    page.goto(pdf_url, timeout=20000)
                    consent_clicked = False
                    for sel in (
                        'input[value="OK"]',
                        'input[type="submit"][value="OK"]',
                        'button:has-text("OK")',
                    ):
                        if page.locator(sel).count() > 0:
                            print(f"  Attempt {attempt}: consent gate detected "
                                  f"on {page.url}; clicking OK")
                            try:
                                page.locator(sel).first.click()
                                page.wait_for_load_state("networkidle",
                                                         timeout=15000)
                                consent_clicked = True
                            except Exception as click_err:
                                print(f"  ! Consent click failed: {click_err}",
                                      file=sys.stderr)
                            break
                    if not consent_clicked:
                        snap = doc_dir / "pdf_fetch_response.html"
                        snap.write_text(page.content())
                        print(f"  ! No consent gate and no download on "
                              f"attempt {attempt} (page snapshot saved to "
                              f"{snap})", file=sys.stderr)
                        break
                except Exception as e:
                    if ("Download is starting" in str(e)
                            or "ERR_ABORTED" in str(e)):
                        break
                    print(f"  ! Attempt {attempt} error: {e}", file=sys.stderr)
                    if "ERR_NAME_NOT_RESOLVED" in str(e):
                        time.sleep(2)
                        continue
                    break

            for _ in range(60):
                if "dl" in download_holder:
                    break
                time.sleep(0.5)

            if "dl" in download_holder:
                dl = download_holder["dl"]
                suggested = dl.suggested_filename or f"{sol_num}.pdf"
                pdf_path = doc_dir / suggested
                try:
                    dl.save_as(str(pdf_path))
                    size = pdf_path.stat().st_size
                    print(f"  ✓ downloaded {pdf_path.name} ({size:,} bytes)")
                    with open(pdf_path, "rb") as f:
                        head = f.read(5)
                    if head != b"%PDF-":
                        print(f"  ! Downloaded file does not start with %PDF-",
                              file=sys.stderr)
                        pdf_path = None
                except Exception as e:
                    print(f"  ! Save failed: {e}", file=sys.stderr)
                    pdf_path = None
            else:
                print("  ! No download event fired after retries.",
                      file=sys.stderr)

        browser.close()

    return pdf_path, meta


# -----------------------------------------------------------------------------
# PDF + clause processing
# -----------------------------------------------------------------------------

def extract_item_info_from_pdf(text: str) -> dict:
    """
    DIBBS listing scraping is unreliable (legacy ASPX HTML). The RFQ PDF
    itself is a much better source for item/NSN — DLA uses consistent
    formatting on Standard Form 18.

    Returns a dict with any of: nsn, nomenclature, quantity, unit_of_issue,
    fsc. Populates whatever it can find; missing keys are simply absent.
    """
    info: dict = {}

    nsn_match = re.search(r"\b(\d{4}-\d{2}-\d{3}-\d{4})\b", text)
    if nsn_match:
        info["nsn"] = nsn_match.group(1)
        info["fsc"] = info["nsn"].split("-")[0]

    for pat in (
        r"NOMENCLATURE[:\s]+([A-Z][A-Z ,/\-()]{4,80})",
        r"ITEM\s+DESCRIPTION[:\s]+([A-Z][A-Z ,/\-()]{4,80})",
        r"([A-Z][A-Z ,/\-()]{4,80}?)\s*(?:NSN|N S N)",
    ):
        m = re.search(pat, text)
        if m:
            nom = re.sub(r"\s{2,}", " ", m.group(1)).strip()
            if len(nom) >= 5 and nom not in {"ITEM", "DESCRIPTION", "NOMENCLATURE"}:
                info["nomenclature"] = nom
                break

    qty = re.search(r"\b(?:QUANTITY|QTY)[:\s]+(\d+)\s*(EA|LB|OZ|FT|IN|SET|KT|PR)?",
                    text, re.I)
    if qty:
        info["quantity"] = qty.group(1)
        if qty.group(2):
            info["unit_of_issue"] = qty.group(2).upper()

    return info


def extract_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("ERROR: pypdf is required. Run: pip install pypdf", file=sys.stderr)
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        print(f"  ! Could not read {path.name}: {e}", file=sys.stderr)
        return ""


def find_clauses(text: str):
    findings = {}
    for label, pat in CLAUSE_PATTERNS:
        matches = pat.findall(text)
        if not matches:
            if pat.search(text):
                findings[label] = {"Present"}
            continue
        norm = set()
        for m in matches:
            if isinstance(m, tuple):
                m = next((x for x in m if x), "")
            norm.add(m.strip() if m else "Present")
        findings[label] = norm
    return findings


def write_json(path: Path, obj) -> None:
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)


def write_requirements_csv(path: Path, sol_num: str, per_doc: dict,
                           meta: dict | None = None) -> None:
    """
    Write the requirements CSV. Each row carries the full solicitation
    context (item, NSN, FSC, agency) so downstream consumers — like the
    teaming-partner-finder skill in Claude chat — have everything they
    need from a single file, without needing the metadata JSON too.
    """
    meta = meta or {}
    item = meta.get("nomenclature", "")
    nsn = meta.get("nsn", "")
    fsc = nsn.split("-")[0] if nsn else ""
    agency = "DLA"

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Solicitation", "Item", "NSN", "FSC", "Agency",
            "Clause Type", "Reference", "Category", "Source Document"
        ])
        for src, findings in per_doc.items():
            for label, refs in sorted(findings.items()):
                cat = CATEGORY_MAP.get(label, "Other")
                for ref in sorted(refs):
                    w.writerow([sol_num, item, nsn, fsc, agency,
                                label, ref, cat, src])


# -----------------------------------------------------------------------------
# Callable entry point (module-level, for programmatic use)
# -----------------------------------------------------------------------------

def extract(sol_num: str, outdir: Path, *, pdf: Path | None = None,
            headed: bool = False, silent: bool = False) -> dict:
    """Run the full extract flow for one solicitation.

    Returns a summary dict with keys:
      - solicitation
      - status: "ok" | "no_pdf" | "no_text" | "error"
      - requirements_csv: Path (only when status == "ok")
      - clauses_json: Path (only when status == "ok")
      - metadata_json: Path (only when status == "ok")
      - findings: {label: [refs...]}
      - error: str (only when status == "error")

    `silent=True` suppresses the summary print at the end but preserves
    the per-step progress lines the underlying script writes (they can
    be redirected by the caller if desired).
    """
    sol = sol_num.strip().upper()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    doc_dir = outdir / f"{sol}_docs"
    doc_dir.mkdir(exist_ok=True)

    try:
        if pdf is not None:
            pdf_path = Path(pdf)
            if not pdf_path.exists():
                return {"solicitation": sol, "status": "error",
                        "error": f"local PDF not found: {pdf}"}
            meta = {"note": "listing skipped; local PDF supplied"}
        else:
            pdf_path, meta = fetch_via_browser(sol, doc_dir, headed=headed)
    except Exception as e:
        return {"solicitation": sol, "status": "error", "error": str(e)}

    if not pdf_path:
        if meta:
            write_json(outdir / f"{sol}_metadata.json", meta)
        return {"solicitation": sol, "status": "no_pdf",
                "metadata_json": outdir / f"{sol}_metadata.json" if meta else None}

    text = extract_text(pdf_path)
    if not text.strip():
        return {"solicitation": sol, "status": "no_text"}

    findings = find_clauses(text)

    pdf_info = extract_item_info_from_pdf(text)
    for k, v in pdf_info.items():
        meta.setdefault(k, v)

    metadata_json = outdir / f"{sol}_metadata.json"
    clauses_json = outdir / f"{sol}_clauses.json"
    requirements_csv = outdir / f"{sol}_requirements.csv"

    write_json(metadata_json, meta)
    per_doc = {pdf_path.name: {k: sorted(v) for k, v in findings.items()}}
    write_json(clauses_json, per_doc)
    write_requirements_csv(requirements_csv, sol, per_doc, meta)

    if not silent:
        print(f"\nSaved outputs to {outdir}/")

    return {
        "solicitation": sol,
        "status": "ok",
        "requirements_csv": requirements_csv,
        "clauses_json": clauses_json,
        "metadata_json": metadata_json,
        "findings": {k: sorted(v) for k, v in findings.items()},
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("solicitation")
    ap.add_argument("--outdir", default="./dibbs_output")
    ap.add_argument("--pdf", default=None,
                    help="Skip DIBBS; parse a local PDF instead")
    ap.add_argument("--headed", action="store_true",
                    help="Show the browser window (for debugging)")
    args = ap.parse_args()

    result = extract(
        args.solicitation,
        Path(args.outdir),
        pdf=Path(args.pdf) if args.pdf else None,
        headed=args.headed,
    )

    if result["status"] == "error":
        print(f"\nERROR: {result['error']}", file=sys.stderr)
        sys.exit(2)
    if result["status"] == "no_pdf":
        print("\nCould not obtain the RFQ PDF. Metadata (if any) saved.",
              file=sys.stderr)
        sys.exit(2)
    if result["status"] == "no_text":
        print("  ! No extractable text (may be scanned PDF; would need OCR).",
              file=sys.stderr)
        sys.exit(3)

    findings = result["findings"]
    print(f"  extracted {sum(len(v) for v in findings.values())} clause reference(s)")

    print("\n" + "=" * 60)
    print(f"COMPLIANCE SUMMARY for {result['solicitation']}")
    print("=" * 60)
    for label in sorted(findings):
        refs = findings[label]
        cat = CATEGORY_MAP.get(label, "Other")
        print(f"  [{cat}] {label}: {', '.join(refs)}")

    print(
        "\n" + "-" * 60 + "\n"
        "REMINDER: The DLA Master Solicitation is INCORPORATED BY REFERENCE.\n"
        "Standard clauses (Section 889, most DFARS flowdowns, MIL-STD marking,\n"
        "cyber/CUI at applicable thresholds) apply even when this RFQ PDF\n"
        "doesn't list them individually. Full text:\n"
        "  https://www.dla.mil/HQ/Acquisition/DLADSolicitations/\n"
        + "-" * 60
    )


if __name__ == "__main__":
    main()
