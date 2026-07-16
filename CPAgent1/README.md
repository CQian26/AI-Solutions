# CPAgent1 — BidMatch RFQ Extractor

Proof-of-concept RFQ opportunity pipeline for **CP Industries** (DoD tooling
manufacturer). Walks the Outreach Systems BidMatch portal, parses each
opportunity, prices via **DIBBS Section A** per-buy history + USASpending
fallbacks, and writes a 5-sheet executive Excel workbook triaged by
estimated contract value.

## Status

- **Intake**: deep-parse portal articles for NSN, NAICS, PSC, qty, set-aside, contact, description
- **Pricing (High)**: RFQ PDF Section A per-buy unit prices from `dibbs2.bsm.dla.mil` × current qty
- **Pricing (Medium)**: USASpending NSN-in-description fuzzy match (magnitude only)
- **Pricing (Low)**: USASpending PSC category proxy — labeled "category proxy (not part price)"
- **Triage**: Over $20K / Under $20K / Needs Pricing / Agent 2 Handoff sheets with `$5M` sanity ceiling
- **Output**: single-page Flask UI on port 8000, or CLI `bidmatch_YYYY-MM-DD.xlsx`
- **Phase B (DIBBS PDF retrieval past the RFQ)**: stubbed behind ITAR review

## Requirements

- Python 3.11+
- `BIDMATCH_SUB` (required) — magic-link token from the daily APEX email
- `SAM_API_KEY` (optional) — free key from https://sam.gov/profile/details
- DIBBS + dibbs2 public scraping works without credentials (consent-banner POST handled internally)

## Setup

```powershell
git clone https://github.com/tmaldon2/CPAgent1
cd CPAgent1
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env, set BIDMATCH_SUB (and optionally SAM_API_KEY)
```

## Running

### Web UI (recommended)

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m bidmatch.web.app
# Logs: "Running on http://0.0.0.0:8000"
# Open http://localhost:8000 in a browser
```

The page asks for your BidMatch URL and a days-window (1-30), streams log
lines while walking the portal, and shows a **Download .xlsx** button when
done. Token stays in your browser + the server process — never written to disk.

Server binds to `0.0.0.0:8000` so teammates on the same LAN can reach it at
`http://<your-IP>:8000`. **No authentication.** Run on trusted networks only.

### CLI

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m bidmatch.cli --days 7 --output today.xlsx
```

Flags:
- `--days N` — lookback window (default 7)
- `--output PATH` — workbook path
- `--verbose` — debug logs (token stays redacted)
- `--no-cache` — skip the on-disk cache
- `--skip-pricing` — offline mode; deep parse only
- `--skip-sam` — don't hit SAM.gov even when key is set

## Autorun (daily 8am)

An unattended daily job keeps one rolling workbook per calendar week up to
date and emails a summary after every run.

### `.env` keys

| Key | Meaning |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` | Outgoing mail server (port defaults to 587) |
| `SMTP_USER` / `SMTP_PASSWORD` | SMTP auth (optional if the relay allows anonymous send) |
| `NOTIFY_TO` | Comma-separated recipient list for success/failure emails |
| `AUTORUN_DAYS` | Lookback window for the daily UPDATE walk (default 2) |
| `AUTORUN_OUTPUT_DIR` | Where the weekly workbook, state file, and log are written (default `output`) |
| `CP_SET_ASIDES` | Comma-separated set-aside categories CP Industries qualifies for (default `small_business`) |

`SMTP_HOST` and `NOTIFY_TO` are required — `load_autorun_config()` raises if either is missing.

### One-time setup

Register the Windows Task Scheduler job **once, as Administrator**:

```powershell
.\ops\register_task.ps1
```

This is a deliverable script only — running it (and any admin/machine-policy
approval that requires) is a manual step for whoever owns the scheduled task;
it is not run automatically as part of this project's install or tests. It
creates a "BidMatch Daily Autorun" task that fires at 8:00am daily, wakes the
machine if needed, and runs the missed occurrence if the machine was off.
The task's action is a small inline `python -c "..."` wrapper that inserts
`src` onto `sys.path` itself (Task Scheduler doesn't inherit the registering
shell's `$env:PYTHONPATH`), so no environment variable setup is required —
just the repo's `.venv` and `.env`.

### Weekly file naming

Each calendar week (Monday-anchored) gets one workbook and one JSON sidecar
state file in `AUTORUN_OUTPUT_DIR`:

- `output/bidmatch_week_<monday>.xlsx` — the workbook, overwritten in place each run
- `output/bidmatch_week_<monday>.state.json` — per-opportunity fingerprint state used to detect real pricing changes across runs

### Monday INIT vs. daily UPDATE

- **Monday (or first run of the week, if no state file exists yet): INIT** — walks a full 10-day window of the portal and prices every opportunity found, seeding that week's state file from scratch.
- **Tuesday–Sunday: UPDATE** — walks only the short `AUTORUN_DAYS` window, adds any newly seen opportunities, re-prices every still-open row already in state (via a fingerprint comparison so unrelated field noise doesn't count as a change), and freezes rows whose due date has passed (left untouched, not re-priced).

### Notifications

- **Success** — an email with a subject line summarizing the day's delta (new / repriced) and Bid/Investigate counts, a body with the full delta breakdown (new, repriced, unchanged, frozen) plus the Bid/No Bid/Investigate split and high-confidence Bid pipeline value, and the week's workbook attached.
- **Failure** — the pipeline never raises past `main()`; any exception is caught, logged, and emailed with the stage it failed at (`config`, `pipeline`, `workbook`, or `notify`) and a redacted traceback (the BidMatch token is never included in the body).

### Logs

Every run appends to `output/autorun.log` (or `<AUTORUN_OUTPUT_DIR>/autorun.log`),
independent of whether the run succeeded or failed — check it first when
troubleshooting a scheduled run.

## Output workbook

Five sheets, executive format:

| Sheet | Contents |
|---|---|
| Summary | Counts, pipeline value, confidence mix, buyer/route breakdown |
| Bid | `decision == "Bid"` — set-aside and value clear the bar; sorted by value desc, confidence-colored |
| No Bid | `decision == "No Bid"` — set-aside excludes CP or another disqualifying rule fired |
| Investigate | Everything else — ambiguous set-aside, no comparable pricing, or over the sanity ceiling |
| Agent 2 Handoff | Every row queued for supplier ID with provenance-marked controlled fields |

Every row's **Source** cell is a clickable hyperlink back to the BidMatch article.
Confidence values (High/Medium/Low) render as colored chips. The **Decision Reason**
column on each triage sheet spells out why a row landed there (e.g. the specific
set-aside rule or pricing gap that drove the Bid/No Bid/Investigate call).

## Confidence tiers

| Tier | Source | Notes |
|---|---|---|
| **High** | DIBBS Section A per-buy unit price × current qty | Real per-unit anchor |
| **Medium** | USASpending NSN-in-description fuzzy, ≤5y | Award magnitude, not a unit price |
| **Low** | USASpending PSC category proxy | `value_basis="category proxy (not part price)"` |
| **None** | No comparable awards | Row routes to Needs Pricing |

Rules:
- **NAICS+PSC never promotes** out of Low regardless of observation count.
- **`$5M` sanity ceiling** — any triage total over $5M routes to Needs Pricing.
- **No quantity → Needs Pricing** — when Section A returns a unit price but the current solicitation has no `qty_value`, the row routes to Needs Pricing with `ceiling_flag = "no quantity - cannot scale"`. A unit price is never multiplied by an unknown quantity.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -v
```

All unit tests run against captured fixtures — no live network. The final
live smoke test against the portal is a separate, gated step.

## DIBBS compliance extractor (Agent 2 Handoff)

Every DIBBS solicitation on the Agent 2 Handoff sheet is now enriched with
the full set of compliance clauses parsed from its RFQ PDF — FAR/DFARS
citations, MIL-STD/PRF/DTL specs, Section 889, ITAR/EAR, TAA/Buy American,
Distribution Statements, and more. The extractor drives a headless
Chromium (via Playwright) through the DoD consent gate, downloads the
solicitation PDF from `dibbs2.bsm.dla.mil`, and writes a per-sol
`<SOL>_requirements.csv` next to the workbook. The **Requirements CSV**
column on the Agent 2 Handoff sheet hyperlinks straight to that file.

One-time setup:

```powershell
pip install playwright pypdf
playwright install chromium
```

The pipeline runs the extractor as part of `bidmatch.cli` by default;
add `--skip-compliance` to disable it, or `--compliance-dir DIR` to
choose where the per-sol files land (defaults to `<output-parent>/compliance`).

### Standalone augment mode

Already have an Agent 2 Handoff CSV and just want to bolt on the
Requirements CSV column? Use the augment CLI directly:

```powershell
python -m bidmatch.compliance.augment_handoff bidmatch_v2_3d_Agent_2_Handoff.csv \
    --outdir ./compliance \
    --output bidmatch_v2_3d_Agent_2_Handoff_augmented.csv
```

Rows without a solicitation number, or whose sol is non-DIBBS (state
portals, non-DLA agencies), are left untouched with a short reason in
the new `Compliance Status` column. Cached CSVs are reused across
reruns — pass `--force` to regenerate.

### Direct per-sol invocation

The extractor is also usable stand-alone (identical to the original
`dibbs_compliance_extractor.py` script):

```powershell
python -m bidmatch.compliance.dibbs_extractor SPE7L426T5346 --outdir ./results
```

## Roadmap toward AWS Bedrock GovCloud

- KMS-backed secret retrieval (replace .env)
- Lambda + Step Function on a daily schedule
- S3 output instead of local disk
- Console-script entrypoint (drop `PYTHONPATH` workaround)
- PUBLOG account for FLIS technical enrichment
