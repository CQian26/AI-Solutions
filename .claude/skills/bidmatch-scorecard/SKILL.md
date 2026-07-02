---
name: bidmatch-scorecard
description: Given a bidmatch email listing federal contract opportunities, look each one up on SAM.gov, extract FAR/DFARS/DLAD clauses (including from attachments), and produce a multi-sheet supplier compliance scorecard xlsx ranking every clause and MIL-SPEC by how often it appears. Use when the user asks to "score bidmatch contracts", "build a supplier scorecard", "find common FAR/DFARS codes across contracts", "analyze compliance requirements across solicitations", pastes a bidmatch email and asks what codes matter, or wants a compliance-profile view over a batch of federal opportunities.
---

# BidMatch Supplier Scorecard

Turn a bidmatch email into a supplier compliance scorecard: parse contract
opportunities, look each up on SAM.gov, extract every FAR/DFARS/DLAD clause
plus MIL-SPEC citations from the description and attachments, roll everything
into an xlsx that ranks codes by how many contracts they appear in.

## When to invoke

Any of these signals should trigger this skill:
- The user pastes a **bidmatch email** or contract-opportunity list and asks
  for analysis, clauses, codes, or compliance requirements.
- The user says "score these contracts", "build a supplier scorecard",
  "which FAR/DFARS clauses matter", or similar.
- The user asks how to prepare a supplier-compliance profile for a set of
  federal opportunities.

## What this skill produces

A `.xlsx` at `supplier_scorecard/output/supplier_scorecard.xlsx` with:

- **Scorecard** — ranked clauses (Citation · Part · Title · What it means · Count · Contract IDs)
- **Contract × Clause** — ✓ matrix
- **MIL-Specs & Standards** — technical standards
- **Raw Contracts** — per-contract metadata
- **Dropped Clauses** — audit trail of filtered-out boilerplate

## Steps to execute

Follow these steps in order. Ask for confirmation before writing to input.txt
if it already contains non-header content the user might want to preserve.

### 1. Verify the module is present

Check that `supplier_scorecard/run.py` exists at the current working directory.
If not, tell the user this skill requires the AI-Solutions repo — they should
`cd` into it or clone it first.

### 2. Ask for the bidmatch content

Ask the user to paste the bidmatch email. Give them the format so they know
what's parseable:

> Paste your bidmatch email content. Each line should look like one of:
>
> `10 -- Suppressors 5.56 NATO (FBI-JEH, WASHINGTON DC)`
> `25 -- BRACKET 9EA NSN 2590017012434 SOL SPE7L026T0325 PR 7016916362 DUE 06/11/26 AMSC G (DLA)`
>
> Lines that don't start with an FSC code (2-4 digits + `--` or `-`) will be
> ignored. Header/comment text is fine to include; it gets skipped.

### 3. Write it to `supplier_scorecard/input.txt`

Use the Write tool to save the pasted content to `supplier_scorecard/input.txt`.
Preserve the instructional header at the top of the file (the block enclosed
in `# ───...` bars). If the user's paste already includes a header, just use
their paste verbatim.

### 4. Install dependencies on first run

Run `pip install -r supplier_scorecard/requirements.txt` if `openpyxl` isn't
already importable. Silent success or the pip output is fine — don't linger.

### 5. Verify live SAM.gov is reachable — DO NOT SIMULATE

This pipeline exists to produce real supplier scorecards. Simulated data
is worse than no data because it looks plausible and would drive real
bid/no-bid decisions. **Never run in mock mode unless the user
*explicitly* asks for it AND acknowledges it's simulated.**

Check the environment:

```bash
echo "${SAM_API_KEY:-}"
curl -sS -m 10 -o /dev/null -w "%{http_code}" "https://api.sam.gov/opportunities/v2/search?limit=1" 2>&1
```

- **If `SAM_API_KEY` is empty**: STOP. Tell the user:
  > This pipeline only produces real scorecards from live SAM.gov data.
  > Get a free key at
  > <https://open.gsa.gov/api/get-opportunities-public-api/>, then
  > `export SAM_API_KEY=xxxxx` and try again.

  Do **not** offer to run in mock mode as a substitute. If the user asks
  for a demo anyway, warn them the output is fabricated and require them
  to explicitly say something like "yes, I want simulated data" before
  proceeding — and even then, the pipeline itself will require the
  `--i-understand-this-is-simulated` flag.

- **If the API key is set but `api.sam.gov` is unreachable** (any status
  other than 2xx/4xx from the curl above, e.g. 403 from a proxy, network
  timeout): STOP. Tell the user:
  > The environment can't reach api.sam.gov (`<the actual error>`).
  > Run this pipeline in an environment that has outbound HTTPS access to
  > sam.gov — your laptop, a self-hosted Claude Code, or any VM you
  > control. The `.claude/skills/bidmatch-scorecard/` skill will work
  > there the same way it would here.

  Some environments (including Claude on the web) block outbound access
  to sam.gov by policy — this is not something you can work around.

- **Only if both checks pass**: proceed to step 6.

### 6. Run the pipeline (live only)

From the `supplier_scorecard/` directory:

```bash
python3 run.py
```

The run reads `input.txt` and `filters.json` by default, hits
api.sam.gov for every opportunity, downloads and scans attachments, and
writes `output/supplier_scorecard.xlsx` + `output/supplier_scorecard.json`.
Rate-limits at 1.5s between requests; a 60-line email takes 60-90s live.

If the pipeline errors mid-run (401 = bad key, 429 = rate-limited,
network error), stop and tell the user what happened — do not fall back
to mock data.

### 7. Report a summary + deliver the file

Load the workbook with openpyxl and print:

- Total opportunities parsed / scored / skipped-truncated / skipped-off-SAM
- Top 10 rows of the Scorecard sheet (citation, count, "What it means")
- Top 5 MIL-Specs
- Count of dropped clauses (link to Dropped Clauses sheet)

Then use `SendUserFile` to deliver `supplier_scorecard/output/supplier_scorecard.xlsx`.

## Common follow-ups the user may ask

**Filter more aggressively** → Edit `supplier_scorecard/filters.json`:
- Add citations to `drop_admin_boilerplate.citations` to remove them
- Set `drop_below_count.enabled: true` and `min_contracts: N` to drop the tail
- Add regulation acronyms to `drop_regulations.regulations` (e.g.
  `["NFS", "HSAR", "JAR"]`) to remove entire agency supplements
- Add specific citations to `drop_specific.citations`

Then re-run — no need to re-scrape SAM.gov if the input hasn't changed
(demo mode is instant either way).

**Explain a clause** → the "What it means" column carries a one-sentence
capability statement. If it's blank for a citation, add an entry to
`clause_extractor.EXPLANATIONS` and re-run.

**Add more clauses to the extractor** → known clause titles live in
`clause_extractor.KNOWN_TITLES`. Add entries so newly-cited clauses get
titled instead of appearing with a blank Title.

**Support a new agency supplement** → add an acronym → Part-prefix pair to
`_AGENCY_MAP` in `clause_extractor.py`; the regex fires automatically.

## Troubleshooting

- **`SAM_API_KEY is not set`** in a non-mock run → export the key or use
  `--mock-dir sample/mock_sam`.
- **`Cannot import openpyxl` / `pdfminer` / `pypdf` / `docx`** →
  `pip install -r supplier_scorecard/requirements.txt`. openpyxl is required;
  the others are optional (attachment-scanning fallback path exists).
- **"No match on SAM.gov"** for a solicitation → confirm the number is
  current (SAM.gov archives close ~6 months after award). Try searching by
  title instead by clearing the `solicitation` field in the parsed opportunity.
- **State/local portals** (Maryland eMaryland, METRA, Fort Worth) aren't on
  SAM.gov — those lines are auto-skipped. The pipeline is federal-only.
- **Truncated bidmatch lines** (`10 -- 10--COVER ASSEMBLY,MACH`) with no SOL#
  can't be searched. Use `--include-untitled` to force scanning (title-based
  SAM.gov search rarely hits on these).

## Notes for Claude

- **Never invent clause citations or explanations.** If the user asks about
  a citation the extractor didn't find, look it up (`grep -n "FAR 52.X" .`
  under `supplier_scorecard/`) or fetch the acquisition.gov reference —
  don't fabricate.
- **Never guess or fabricate a SAM.gov API response.** If the network can't
  reach `api.sam.gov`, STOP and tell the user (see step 5). Do not silently
  fall back to mock data. Do not synthesize what a plausible response
  "would" contain. The output of this pipeline is used to drive real
  bid/no-bid decisions; fabricated data is worse than no data.
- **The pipeline enforces this too.** `run.py` refuses to accept
  `--dev-only-mock-dir` without `--i-understand-this-is-simulated`, and any
  simulated xlsx gets a large red "!! SIMULATED DATA !!" cover sheet the
  user cannot miss.
- **File-write scope.** Only write to `supplier_scorecard/input.txt`,
  `supplier_scorecard/filters.json` (when editing filters), and
  `supplier_scorecard/output/`. Everything else is source code — edit only if
  the user explicitly asked for a feature change.
- **The `.xlsx` is the deliverable.** After every real run, call
  `SendUserFile` on `supplier_scorecard/output/supplier_scorecard.xlsx` so
  the user sees it even if they don't have file-system access to the
  container.
