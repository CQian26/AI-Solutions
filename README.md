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

### First-time setup

Two things the skill will prompt about on the first run:

- **Python deps** — `pip install -r supplier_scorecard/requirements.txt`
  (`openpyxl` is required; `pdfminer.six` / `pypdf` / `python-docx` are for
  PDF/DOCX attachment scanning).
- **SAM.gov API key** — get a free one at
  <https://open.gsa.gov/api/get-opportunities-public-api/>, then
  `export SAM_API_KEY=xxxxx`. Without a key the skill can still run in **demo
  mode** against bundled mock responses (`--mock-dir sample/mock_sam`) so you
  see the shape of the output before wiring in live data.

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
