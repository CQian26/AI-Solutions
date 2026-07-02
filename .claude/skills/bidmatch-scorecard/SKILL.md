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

### 5. Decide live-vs-mock mode

Check `echo "${SAM_API_KEY:-}"`:

- **If a key is set**: proceed live against SAM.gov. The pipeline reads it
  from the environment automatically.
- **If empty**: tell the user:
  > No `SAM_API_KEY` in your environment. Two options:
  > 1. **Demo mode** — run against the bundled mock SAM.gov responses so you
  >    see the shape of the output (`--mock-dir sample/mock_sam`). Clauses
  >    will reflect template profiles, not the actual live opportunity text.
  > 2. **Live mode** — get a free key at
  >    https://open.gsa.gov/api/get-opportunities-public-api/, then set
  >    `export SAM_API_KEY=xxx` and re-run.
  >
  > Which would you like?

  Default to demo mode if they say "just show me" or similar.

### 6. Run the pipeline

From the `supplier_scorecard/` directory:

- Live: `python3 run.py`
- Demo: `python3 run.py --mock-dir sample/mock_sam`

The run defaults to `input.txt` and `filters.json`, writes
`output/supplier_scorecard.xlsx` and `output/supplier_scorecard.json`.
Rate-limits SAM.gov requests by default; a 60-line email takes 60-90s live.

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
- **Never guess a live SAM.gov API response.** If the network can't reach
  `api.sam.gov`, either use the mock dir or ask the user to run the pipeline
  in an environment that can, then paste the resulting `scorecard.json` back.
- **File-write scope.** Only write to `supplier_scorecard/input.txt`,
  `supplier_scorecard/filters.json` (when editing filters), and
  `supplier_scorecard/output/`. Everything else is source code — edit only if
  the user explicitly asked for a feature change.
- **The `.xlsx` is the deliverable.** After every run, call `SendUserFile` on
  `supplier_scorecard/output/supplier_scorecard.xlsx` so the user sees it
  even if they don't have file-system access to the container.
