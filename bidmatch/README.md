# BidMatch Tag Scorecard

Take a **bidmatch list** (a CSV of contract opportunities), **scrape each
contract's tags** from the official website, **score** those tags against your
**required specifications**, and view a ranked **score card** — including a
radar chart per contract.

```
bidmatch list (CSV)  ─►  scrape tags  ─►  score vs. specifications  ─►  scorecard.json  ─►  Scorecard.html
```

The pipeline is built so you can point it at any procurement portal (SAM.gov, a
state portal, Washington BidMatch, etc.) by editing two config files — no code
changes needed.

---

## Quick start (offline demo)

No network or dependencies required — runs against the bundled sample pages:

```bash
cd bidmatch
python3 scrape_bidmatch.py --list sample_bidmatch_list.csv --out scorecard.json
```

Then open **`../Scorecard.html`** in a browser and click **“Load scorecard.json”**
(or serve the repo over http and click “Fetch bidmatch/scorecard.json”).

You'll see the six sample contracts ranked, with the AI/analytics platform on
top and the unrelated grounds-maintenance contract scoring 0%.

---

## Using it for real

### 1. Build your bidmatch list — `my_list.csv`

```csv
id,title,url
BM-1001,Some Opportunity,https://portal.example.gov/opportunity/1001
BM-1002,Another Opportunity,https://portal.example.gov/opportunity/1002
```

- `url` must be an absolute `http(s)` link to the contract's page (or a local
  file path, as the samples use).
- `id` and `title` are for display; `title` falls back to the scraped `<h1>`.

### 2. Tell the scraper how to read that site — `site_config.json`

Open a contract page in your browser, **Inspect** the tag/keyword elements, and
put their CSS selectors under `selectors`. Every selector is optional — if one
finds nothing, the scraper falls back to scanning the full page text, so it
still works with no selectors at all. `request` controls user-agent, timeout,
retry, and the polite delay between requests; `robots.respect_robots_txt`
(default `true`) honors the site's robots.txt.

### 3. Define what “a good match” means — `specifications.json`

This is the scoring rubric: categories (each becomes a **radar axis**) of
weighted tags. A contract scores on a tag if that tag — or any of its
`aliases` — appears in the scraped tags/text (whole-word, case-insensitive).
Edit it to reflect **your** company's capabilities and the criteria you bid on.

### 4. Run

```bash
pip install -r requirements.txt        # optional: precise selector extraction
python3 scrape_bidmatch.py \
    --list my_list.csv \
    --specs specifications.json \
    --site-config site_config.json \
    --out scorecard.json
```

Load the resulting `scorecard.json` into `Scorecard.html`.

---

## How scoring works

For each contract the scraper assembles a haystack of *title + agency + scraped
tags + description text*. Then, per specification category:

```
category_score (0–10) = (sum of weights of matched tags in the category)
                        / (sum of all tag weights in the category) * 10

overall_score (%)     = (total matched weight across all categories)
                        / (total weight) * 100
```

Contracts are ranked by `overall_score`, descending. The radar chart plots the
six category scores so you can see *where* a contract fits your profile, not
just the headline number.

---

## Files

| File | Purpose |
|------|---------|
| `scrape_bidmatch.py` | The pipeline: scrape → score → write `scorecard.json`. |
| `specifications.json` | Required-specifications rubric (categories, tags, weights). **Edit this.** |
| `site_config.json` | Per-site scraper selectors + request settings. **Edit this.** |
| `sample_bidmatch_list.csv` | Example input list (points at the sample pages). |
| `sample_pages/` | Offline mock contract pages so the demo runs with no network. |
| `scorecard.json` | Generated output (consumed by `Scorecard.html`). |
| `../Scorecard.html` | The score card UI: ranked table + per-contract radar chart. |

---

## Scraping responsibly

Only scrape sites you're permitted to. The scraper honors `robots.txt` by
default, sends a descriptive user-agent, retries with backoff, and waits
between requests. Set a realistic `delay_seconds_between_requests` and don't
disable the robots check unless you have explicit permission from the site
owner. Many portals (e.g. SAM.gov) also offer official APIs — prefer those when
available.
