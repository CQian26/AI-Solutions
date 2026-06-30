#!/usr/bin/env python3
"""
scrape_bidmatch.py
==================
Given a bidmatch list (CSV of contract id/title/url), fetch each contract page
from the official website, extract its tags, and score those tags against a
required-specifications profile. Emits a scorecard.json that the bundled
Scorecard.html (table + radar chart) renders.

Pipeline:  bidmatch list  ->  scrape tags  ->  score vs. specifications  ->  scorecard.json

Zero required dependencies: runs on the Python standard library alone.
If `beautifulsoup4` is installed it is used for precise, selector-based tag
extraction; otherwise the script falls back to scanning the full page text for
specification tags (still produces a complete scorecard).

Usage
-----
  # Offline demo against the bundled sample pages:
  python3 scrape_bidmatch.py --list sample_bidmatch_list.csv --out scorecard.json

  # Against a real portal (URLs in the CSV must be absolute http(s) links):
  python3 scrape_bidmatch.py \
      --list my_bidmatch_list.csv \
      --specs specifications.json \
      --site-config site_config.json \
      --out scorecard.json

CSV columns: id,title,url   (a `url` may be an absolute http(s) link or a path
relative to this script's folder, e.g. the bundled sample_pages/*.html).
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))

# --- optional dependency -------------------------------------------------
try:
    from bs4 import BeautifulSoup  # type: ignore
    HAVE_BS4 = True
except Exception:
    HAVE_BS4 = False


# --- tiny stdlib HTML -> text fallback -----------------------------------
class _TextExtractor(HTMLParser):
    """Collapse an HTML document to visible text (skips script/style)."""
    def __init__(self):
        super().__init__()
        self._skip = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data)

    def text(self):
        return " ".join(self.parts)


def html_to_text(html):
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.text()


# --- fetching ------------------------------------------------------------
def is_remote(url):
    return url.lower().startswith(("http://", "https://"))


def robots_allows(url, user_agent):
    """Best-effort robots.txt check. Fails open (returns True) on any error."""
    try:
        parts = urllib.parse.urlsplit(url)
        robots_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


def fetch(url, req_cfg, respect_robots):
    """Return page HTML, or raise. Handles local files and http(s) with retries."""
    if not is_remote(url):
        path = url if os.path.isabs(url) else os.path.join(HERE, url)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    ua = req_cfg.get("user_agent", "BidMatchScorecard/1.0")
    if respect_robots and not robots_allows(url, ua):
        raise PermissionError(f"robots.txt disallows fetching {url}")

    timeout = req_cfg.get("timeout_seconds", 30)
    retries = int(req_cfg.get("max_retries", 3))
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                backoff = 2 ** attempt
                print(f"    fetch failed ({e}); retry {attempt}/{retries} in {backoff}s", file=sys.stderr)
                time.sleep(backoff)
    raise last_err


# --- extraction ----------------------------------------------------------
def extract_fields(html, selectors):
    """Return dict(title, agency, tags[list], text) from a contract page."""
    result = {"title": "", "agency": "", "tags": [], "text": ""}

    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")

        def first_text(sel):
            if not sel:
                return ""
            el = soup.select_one(sel)
            return el.get_text(" ", strip=True) if el else ""

        result["title"] = first_text(selectors.get("title"))
        result["agency"] = first_text(selectors.get("agency"))

        tag_sel = selectors.get("tags")
        if tag_sel:
            for el in soup.select(tag_sel):
                t = el.get_text(" ", strip=True)
                if t:
                    result["tags"].append(t)
                # also capture data-tag attributes
                for v in el.attrs.values():
                    if isinstance(v, str) and v.strip() and v != t:
                        pass  # keep simple; text is the signal

        desc_sel = selectors.get("description")
        desc = first_text(desc_sel) if desc_sel else ""
        result["text"] = desc or soup.get_text(" ", strip=True)
    else:
        # stdlib fallback: no CSS selectors, just full-page text
        result["text"] = html_to_text(html)

    # de-duplicate tags, preserve order
    seen = set()
    deduped = []
    for t in result["tags"]:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(t)
    result["tags"] = deduped
    return result


# --- scoring -------------------------------------------------------------
def _phrase_in(phrase, haystack):
    """Whole-word/phrase, case-insensitive match. Handles 8(a), 541511, ci/cd."""
    p = phrase.strip().lower()
    if not p:
        return False
    # word-boundary on the edges where the edge char is alphanumeric
    left = r"\b" if p[0].isalnum() else ""
    right = r"\b" if p[-1].isalnum() else ""
    return re.search(left + re.escape(p) + right, haystack) is not None


def score_contract(fields, spec):
    """Return (category_scores, matched_by_cat, overall_pct)."""
    haystack = " ".join([fields["title"], fields["agency"], " ".join(fields["tags"]), fields["text"]]).lower()

    category_scores = {}
    matched_by_cat = {}
    total_weight = 0.0
    total_matched = 0.0

    for cat in spec["categories"]:
        cat_total = 0.0
        cat_matched = 0.0
        matched_tags = []
        for entry in cat["tags"]:
            w = float(entry.get("weight", 1))
            cat_total += w
            candidates = [entry["tag"]] + entry.get("aliases", [])
            if any(_phrase_in(c, haystack) for c in candidates):
                cat_matched += w
                matched_tags.append(entry["tag"])
        score10 = round((cat_matched / cat_total) * 10, 1) if cat_total else 0.0
        category_scores[cat["name"]] = score10
        matched_by_cat[cat["name"]] = matched_tags
        total_weight += cat_total
        total_matched += cat_matched

    overall_pct = round((total_matched / total_weight) * 100, 1) if total_weight else 0.0
    return category_scores, matched_by_cat, overall_pct


# --- driver --------------------------------------------------------------
def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_list(path):
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            row = { (k or "").strip().lower(): (v or "").strip() for k, v in row.items() }
            if not row.get("url"):
                continue
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description="Scrape bidmatch contract tags and build a scorecard.")
    ap.add_argument("--list", default=os.path.join(HERE, "sample_bidmatch_list.csv"),
                    help="CSV bidmatch list with columns id,title,url")
    ap.add_argument("--specs", default=os.path.join(HERE, "specifications.json"),
                    help="Required-specifications JSON (the scoring rubric)")
    ap.add_argument("--site-config", default=os.path.join(HERE, "site_config.json"),
                    help="Scraper config (selectors, request settings)")
    ap.add_argument("--out", default=os.path.join(HERE, "scorecard.json"),
                    help="Where to write the scorecard JSON")
    args = ap.parse_args()

    spec = load_json(args.specs)
    site = load_json(args.site_config)
    selectors = site.get("selectors", {})
    req_cfg = site.get("request", {})
    respect_robots = site.get("robots", {}).get("respect_robots_txt", True)
    delay = float(req_cfg.get("delay_seconds_between_requests", 1.0))

    if not HAVE_BS4:
        print("NOTE: beautifulsoup4 not installed -> using full-text fallback "
              "(CSS selectors ignored). Install with: pip install beautifulsoup4\n", file=sys.stderr)

    contracts_in = read_list(args.list)
    print(f"Scoring {len(contracts_in)} contract(s) from {os.path.basename(args.list)}...\n")

    results = []
    for i, row in enumerate(contracts_in, 1):
        cid = row.get("id") or f"row-{i}"
        url = row["url"]
        print(f"[{i}/{len(contracts_in)}] {cid}  {url}")
        try:
            html = fetch(url, req_cfg, respect_robots)
            fields = extract_fields(html, selectors)
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            fields = {"title": row.get("title", ""), "agency": "", "tags": [], "text": ""}

        # CSV title/url take precedence for display if present
        title = row.get("title") or fields["title"] or cid
        cat_scores, matched, overall = score_contract(fields, spec)
        results.append({
            "id": cid,
            "title": title,
            "url": url,
            "agency": fields["agency"],
            "tags": fields["tags"],
            "matched": matched,
            "category_scores": cat_scores,
            "overall_score": overall,
        })
        n_matched = sum(len(v) for v in matched.values())
        print(f"    overall {overall:5.1f}%   ({n_matched} spec tags matched)")
        if is_remote(url) and i < len(contracts_in):
            time.sleep(delay)

    # rank by overall score, descending
    results.sort(key=lambda r: r["overall_score"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "axes": [c["name"] for c in spec["categories"]],
        "contracts": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    print(f"\nWrote {args.out}")
    print("\nRanked scorecard")
    print("-" * 64)
    for r in results:
        print(f"  #{r['rank']:<2} {r['overall_score']:5.1f}%  {r['id']:<14} {r['title'][:34]}")
    print("-" * 64)
    print(f"Open Scorecard.html and load {os.path.basename(args.out)} to view the full card + radar charts.")


if __name__ == "__main__":
    main()
