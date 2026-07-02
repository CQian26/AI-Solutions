# Supplier Scorecard

Given the article list from a **bidmatch email**, look each opportunity up on
**SAM.gov**, harvest every **FAR / DFARS / agency-supplement clause** cited on
its contract page *and* in its attachments, and roll everything into a **3-sheet
supplier-scorecard `.xlsx`** — the codes a supplier's compliance profile must
cover, ranked by how often they appear.

```
bidmatch email  ─►  parse_email.py       (opportunities)
                ─►  sam_client.py         (SAM.gov v2 API → notice + attachment links)
                ─►  attachment_scanner.py (PDF/DOCX → text)
                ─►  clause_extractor.py   (FAR/DFARS citations)
                ─►  scorecard_writer.py   (supplier_scorecard.xlsx)
```

The **`.xlsx`** has three sheets:

| Sheet | What's in it |
|---|---|
| **Scorecard** | One row per unique clause. Citation, regulation, part, number, title, count, coverage %, contract IDs. Sorted by count — the top rows are the codes any supplier profile must cover. |
| **Contract × Clause** | Pivot matrix. Rows = clauses, columns = contracts, ✓ where a clause appears. Great for gap analysis per opportunity. |
| **Raw Contracts** | One row per contract. Title, agency, NAICS, PSC/FSC, set-aside, posted/deadline dates, which sources were scanned (description + which attachments), clause count, SAM.gov URL. |

---

## Quick start — offline demo

Paste your bidmatch email into **`input.txt`** (a starter is already there),
then:

```bash
cd supplier_scorecard
pip install -r requirements.txt         # openpyxl required; the rest are for PDF/DOCX
python3 run.py --mock-dir sample/mock_sam   # reads input.txt by default
```

Or point at a specific file:

```bash
python3 run.py path/to/your_bidmatch_email.txt --mock-dir sample/mock_sam
```

Open **`output/supplier_scorecard.xlsx`**. You'll see 76 unique clauses across
4 mocked contracts, with universal clauses (FAR 52.204-7, DFARS 252.204-7012,
etc.) at the top and contract-specific ones (Kaspersky prohibition, Brand Name
or Equal, Hexavalent Chromium) at the bottom.

---

## Real use — against live SAM.gov

1. **Get a free API key.** SAM.gov's public Opportunities API v2 is fed by
   [api.data.gov](https://open.gsa.gov/api/get-opportunities-public-api/) —
   sign up, verify email, copy your key.

2. **Set the key + run:**
   ```bash
   export SAM_API_KEY=your_key_here
   python3 run.py                     # reads input.txt
   # or: python3 run.py path/to/your_email.txt
   ```

   The input can be plain `.txt`, `.eml`, or `.mbox`. Anything in `input.txt`
   above the first FSC-coded line (e.g. the instructional header) is ignored.

Useful flags:

- `--limit N` — only process the first N opportunities (fast during dev).
- `--no-attachments` — skip attachment download + scanning (much faster; less
  thorough — plenty of clauses live in the SOW/Ts&Cs attachment, so this is a
  speed/completeness trade).
- `--include-untitled` — also search opportunities whose title got truncated
  in the bidmatch email (e.g. `10--COVER ASSEMBLY,MACH`). Off by default
  because they typically produce noisy searches.
- `--rate 2.0` — seconds between SAM.gov requests (default 1.5, be polite).
- `-v` — verbose logging (shows which attachments got scanned and their sizes).

---

## Why the API instead of scraping the SAM.gov site

SAM.gov's opportunity pages are heavy React apps: fetching the HTML gives you
an empty shell, and the real data loads via internal APIs that return JSON
anyway. The **Opportunities API v2** returns the same JSON directly, stable,
documented, and with attachment download URLs built in. It also rate-limits
gracefully (the client backs off on 429/5xx). Full scraping via a headless
browser is possible but slower, more fragile, and unnecessary.

---

## What counts as a "clause"

`clause_extractor.py` recognizes citations of the form:

- **FAR** &nbsp;&nbsp; `52.NNN-NN[A]`
- **DFARS** &nbsp;&nbsp; `252.NNN-NNNN[A]`
- **AFARS** (Army supplement) `5152.NNN-NNNN`
- **AFFARS / DAFFARS** (Air Force) `5352.NNN-NNNN`
- **NFS** (NASA) `1852.NNN-NN`
- **HSAR** (DHS) `3052.NNN-NN`
- **DEAR** (DOE) `952.NNN-NN`
- **VAAR** (VA) `852.NNN-NN`
- **AGAR / EDAR / TAR** — USDA / Dept. of Education / Treasury

Prefixed forms (`FAR 52.204-24`, `DFARS Clause 252.204-7012`,
`FAR Provision 52.212-3`) are always accepted. **Bare** numbers
(`52.204-24` with no prefix) are accepted only when the enclosing document
elsewhere names FAR or DFARS — this avoids treating phone numbers, ZIP+4s,
and dates as clauses.

Titles are looked up from a curated table (`KNOWN_TITLES` in
`clause_extractor.py`) covering the highest-frequency DoD supply-contract
clauses. Unknown clauses still appear in the scorecard with an empty title —
add entries to `KNOWN_TITLES` to enrich the table.

---

## Files

| File | Purpose |
|---|---|
| `parse_email.py` | Parse a `.txt/.eml/.mbox` bidmatch email into opportunities (FSC, title, agency, solicitation#, NSN, PR#, due, AMSC, quantity). |
| `sam_client.py` | SAM.gov Opportunities v2 client (search + download). Rate-limits, retries on 429/5xx, supports a `--mock-dir` for offline runs. |
| `attachment_scanner.py` | PDF (pdfminer.six / pypdf), DOCX (python-docx), and plain-text extraction. |
| `clause_extractor.py` | FAR/DFARS/agency-supplement citation extractor with title lookup. |
| `scorecard_writer.py` | Writes the 3-sheet `.xlsx`. |
| `run.py` | Orchestrator (defaults to reading `input.txt`). |
| `input.txt` | **Paste your bidmatch email here.** Header comments are ignored. |
| `filters.json` | Which clauses to keep vs. move to the "Dropped Clauses" sheet. |
| `samples_from_email/bidmatch_email_1.txt` | Preserved sample for reference. |
| `sample/mock_sam/*.json` | Canned SAM.gov responses so the demo runs offline. |
| `sample/mock_attachments/*.txt` | Bundled "attachments" the mock SAM.gov responses point to. |
| `output/` | Generated `.xlsx` + audit `.json` land here. |

---

## Extending

- **New agency supplement** — add its acronym and Part prefix to `_AGENCY_MAP`
  in `clause_extractor.py`.
- **More clause titles** — append to `KNOWN_TITLES`.
- **A different scorecard shape** — `scorecard_writer.py` is small and
  self-contained; add a fourth sheet, per-agency roll-ups, weighted scoring,
  etc. The input is a plain dict list — trivial to swap in a different writer.
