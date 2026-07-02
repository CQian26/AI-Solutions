# AI-Solutions

Utilities for turning federal-contract bidmatch data into supplier-compliance
insight.

## What's in here

| Path | What it does |
|---|---|
| **`supplier_scorecard/`** | End-to-end pipeline: bidmatch email → SAM.gov lookup → FAR/DFARS clause extraction → multi-sheet scorecard xlsx. See its [README](supplier_scorecard/README.md) for the full docs. |
| **`.claude/skills/bidmatch-scorecard/`** | A **Claude Code skill** so anyone with this repo pulled can invoke the pipeline in natural language — no CLI knowledge required. |
| `Radar_Chart.html` | Interactive React radar chart (unrelated legacy demo — kept for reference). |
| `Scorecard.html` | Browser-based ranked-scorecard viewer (earlier iteration, superseded by the xlsx). |

---

## Using it as a Claude Code skill

Anyone can drive the whole pipeline through conversation instead of the CLI:

1. **Clone the repo** and open it in Claude Code (CLI, desktop, or web).
2. **Ask Claude naturally**, e.g.:
   - "Score this bidmatch email for me"
   - "Build me a supplier scorecard from these contracts"
   - "Which FAR/DFARS clauses come up most across this batch of solicitations?"
3. Claude picks up the skill, **prompts for the bidmatch content**, writes it
   to `supplier_scorecard/input.txt`, runs the pipeline, and returns the
   `.xlsx` scorecard.

The skill file is `.claude/skills/bidmatch-scorecard/SKILL.md`. It auto-loads
because it lives in `.claude/skills/` inside the repo — Claude Code discovers
project-local skills whenever you open a session in the repo directory.

### Where to run it

**On your own machine** (laptop, workstation, self-hosted Claude Code, or any
VM you control). This pipeline calls the SAM.gov API directly, so it needs
outbound HTTPS to `api.sam.gov`.

**Not** in Claude on the web — that environment's egress proxy blocks
`api.sam.gov` by policy, so the skill will detect the block and stop rather
than fabricate a result.

### First-time setup on your laptop

```bash
# 1. Clone the repo
git clone https://github.com/CQian26/AI-Solutions.git
cd AI-Solutions

# 2. Install Python deps
pip install -r supplier_scorecard/requirements.txt

# 3. Get a free SAM.gov API key
#    https://open.gsa.gov/api/get-opportunities-public-api/
export SAM_API_KEY=your_key_here

# 4. Paste your bidmatch email into supplier_scorecard/input.txt

# 5. Run — either via Claude Code (skill picks it up automatically):
claude
# then: "score the contracts in input.txt"
#
# OR directly:
python3 supplier_scorecard/run.py
```

The `.xlsx` scorecard lands at `supplier_scorecard/output/supplier_scorecard.xlsx`.

### Simulation mode is intentionally off by default

The pipeline refuses to fabricate results. If `SAM_API_KEY` is missing or
the network cannot reach `api.sam.gov`, it stops with a clear error. A
`--dev-only-mock-dir` flag exists for testing code changes to the pipeline
itself — it requires `--i-understand-this-is-simulated` and stamps every
resulting workbook with a red **!! SIMULATED DATA !!** cover sheet so no one
mistakes it for a real supplier profile.

---

## Using it without Claude (plain CLI)

Everything the skill does is a shell command underneath:

```bash
cd supplier_scorecard
pip install -r requirements.txt

# 1. Paste your bidmatch email into input.txt
# 2. Run:
python3 run.py --mock-dir sample/mock_sam        # offline demo
SAM_API_KEY=xxxxxx python3 run.py                # live SAM.gov

# 3. Open output/supplier_scorecard.xlsx
```

Full CLI docs, flags, tuning knobs, and extension points live in
[`supplier_scorecard/README.md`](supplier_scorecard/README.md).

---

## Repo layout

```
AI-Solutions/
├── README.md                                    ← you are here
├── .claude/
│   └── skills/
│       └── bidmatch-scorecard/
│           └── SKILL.md                         ← Claude Code skill entrypoint
├── supplier_scorecard/                          ← the pipeline
│   ├── run.py                                   ← orchestrator (reads input.txt)
│   ├── input.txt                                ← ⬅ paste your bidmatch here
│   ├── parse_email.py                           ← bidmatch line parser
│   ├── sam_client.py                            ← SAM.gov Opportunities API v2 client
│   ├── clause_extractor.py                      ← FAR/DFARS/DLAD/... + MIL-SPEC extractor
│   ├── attachment_scanner.py                    ← PDF/DOCX text extraction
│   ├── scorecard_writer.py                      ← xlsx writer (5 sheets)
│   ├── filters.json                             ← which clauses to keep vs. drop
│   ├── requirements.txt
│   ├── README.md
│   ├── sample/                                  ← mock SAM.gov responses + generator
│   ├── samples_from_email/                      ← preserved raw email samples
│   └── output/                                  ← generated .xlsx lands here
├── Radar_Chart.html
└── Scorecard.html
```

---

## Working branch

Development on this feature happens on
[`claude/bidmatch-contract-tags-63ile9`](../../tree/claude/bidmatch-contract-tags-63ile9).
