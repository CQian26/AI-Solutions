#!/usr/bin/env python3
"""
sam_client.py
=============
Thin client for the SAM.gov Opportunities API v2.

Endpoints used:
  GET /opportunities/v2/search          -- search by solicitation# or keyword
  GET <resourceLinks[i]>                 -- download attachments (from a notice)

Requires a free api.data.gov key exported as SAM_API_KEY. Get one at:
  https://open.gsa.gov/api/get-opportunities-public-api/

The API is stable and returns JSON directly — much less fragile than scraping
sam.gov's JS-rendered opportunity pages.

Offline mode (--mock-dir) reads canned JSON responses from disk so the whole
pipeline is testable with no network or key. See ../sample/mock_sam/ for the
schema this expects.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional


SAM_BASE = "https://api.sam.gov/opportunities/v2/search"


@dataclass
class Attachment:
    name: str
    url: str
    mime: Optional[str] = None
    size: Optional[int] = None


@dataclass
class Notice:
    """A slim projection of the SAM.gov opportunity payload."""
    notice_id: str
    solicitation_number: Optional[str]
    title: str
    agency: Optional[str]
    naics: Optional[str]
    classification_code: Optional[str]  # PSC/FSC
    set_aside: Optional[str]
    posted_date: Optional[str]
    response_deadline: Optional[str]
    description: str                    # full opportunity description text
    ui_link: Optional[str]
    attachments: list[Attachment] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class SamClientError(RuntimeError):
    pass


class SamClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        mock_dir: Optional[Path] = None,
        rate_limit_seconds: float = 1.5,
        timeout: int = 30,
        max_retries: int = 3,
        posted_from: Optional[str] = None,
        posted_to: Optional[str] = None,
        lookback_days: int = 365,
    ):
        self.api_key = api_key or os.environ.get("SAM_API_KEY")
        self.mock_dir = Path(mock_dir) if mock_dir else None
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout
        self.max_retries = max_retries
        # SAM.gov v2 /opportunities/search REQUIRES postedFrom/postedTo in
        # MM/dd/yyyy format. Default to today back 'lookback_days' — 365d
        # covers active opportunities (typical posting window is 30-90d) plus
        # recently-archived ones (SAM archives 15d after response deadline).
        today = date.today()
        self.posted_to = posted_to or today.strftime("%m/%d/%Y")
        self.posted_from = posted_from or (today - timedelta(days=lookback_days)).strftime("%m/%d/%Y")
        self._last_request_at = 0.0

    # ---------- low-level HTTP ----------

    def _sleep_between_calls(self):
        elapsed = time.time() - self._last_request_at
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request_at = time.time()

    def _get_json(self, url: str, params: Optional[dict] = None) -> dict:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            self._sleep_between_calls()
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # 429 = rate limited, 5xx = transient; retry with backoff.
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    last_err = e
                    continue
                # Try to surface the SAM.gov API's actual error message.
                body_snippet = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")
                    body_snippet = body[:600].strip().replace("\n", " ")
                except Exception:
                    pass
                # Never leak the api_key in the error message.
                safe_url = re.sub(r"api_key=[^&]+", "api_key=***REDACTED***", url)
                detail = f": {body_snippet}" if body_snippet else ""
                raise SamClientError(
                    f"HTTP {e.code} from SAM.gov: {e.reason} for {safe_url}{detail}"
                ) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
        raise SamClientError(f"SAM.gov request failed after {self.max_retries} attempts: {last_err}")

    def download(self, url: str, dest: Path) -> Path:
        """Download a resource (usually an attachment) to disk. Streams; handles auth."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        # attachments often require the api_key in the query string too
        if self.api_key and "api_key=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={self.api_key}"
        req = urllib.request.Request(url)
        self._sleep_between_calls()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
        except urllib.error.HTTPError as e:
            safe_url = re.sub(r"api_key=[^&]+", "api_key=***REDACTED***", url)
            raise SamClientError(f"HTTP {e.code} downloading {safe_url}: {e.reason}") from e
        return dest

    # ---------- high-level API ----------

    def _resolve_mock(self, key: str) -> Optional[dict]:
        """Load canned JSON response from mock_dir by key. Returns None if missing."""
        if not self.mock_dir:
            return None
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
        candidates = [
            self.mock_dir / f"{safe}.json",
            self.mock_dir / "search" / f"{safe}.json",
        ]
        for p in candidates:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        return None

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        by: str = "title",
        extra_params: Optional[dict] = None,
    ) -> list[dict]:
        """Search opportunities. Returns the list of raw records.

        `by` selects which SAM.gov v2 query field to use:
            "title"  -> partial-match against the opportunity title (default)
            "solnum" -> exact-match against the solicitation number
        """
        # Mock resolution: try both a by-typed key and the plain query so
        # existing mock fixtures keep working.
        mock = self._resolve_mock(f"search_{query}") or self._resolve_mock(f"search_{by}_{query}")
        if mock is not None:
            return mock.get("opportunitiesData", []) or mock.get("results", [])

        # In mock mode, unknown queries return empty (nothing found).
        if self.mock_dir:
            return []

        if not self.api_key:
            raise SamClientError(
                "SAM_API_KEY is not set. Get a free key at "
                "https://open.gsa.gov/api/get-opportunities-public-api/."
            )

        if by not in ("title", "solnum"):
            raise ValueError(f"Unknown 'by' value: {by!r} (want 'title' or 'solnum')")

        # SAM.gov v2 /opportunities/search: postedFrom + postedTo are REQUIRED.
        params = {
            "api_key": self.api_key,
            "limit": str(limit),
            "postedFrom": self.posted_from,
            "postedTo": self.posted_to,
            by: query,
        }
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        data = self._get_json(SAM_BASE, params)
        return data.get("opportunitiesData") or data.get("results") or []

    def search_solicitation(self, sol_number: str) -> Optional[dict]:
        """Search by solicitation number (exact match), return best record or None."""
        results = self.search(sol_number, limit=5, by="solnum")
        for r in results:
            if (r.get("solicitationNumber") or "").upper() == sol_number.upper():
                return r
        return results[0] if results else None

    # ---------- projection ----------

    def to_notice(self, record: dict) -> Notice:
        """Project a raw SAM.gov record into a Notice."""
        # Attachments live under resourceLinks (URLs) — names when available under
        # 'resources'/'attachments'. Names fall back to the URL basename.
        atts: list[Attachment] = []
        names_by_url: dict[str, str] = {}
        for r in (record.get("resources") or record.get("attachments") or []):
            if isinstance(r, dict):
                url = r.get("url") or r.get("resource")
                name = r.get("name") or r.get("fileName")
                if url:
                    if name:
                        names_by_url[url] = name
                    atts.append(Attachment(
                        name=name or url.rsplit("/", 1)[-1],
                        url=url,
                        mime=r.get("mimeType"),
                        size=r.get("size"),
                    ))
        # In some responses only resourceLinks[] is present:
        for url in (record.get("resourceLinks") or []):
            if any(a.url == url for a in atts):
                continue
            atts.append(Attachment(
                name=names_by_url.get(url) or url.rsplit("/", 1)[-1] or "attachment",
                url=url,
            ))

        return Notice(
            notice_id=str(record.get("noticeId") or record.get("id") or ""),
            solicitation_number=record.get("solicitationNumber"),
            title=str(record.get("title") or "").strip(),
            agency=(record.get("fullParentPathName")
                    or record.get("department")
                    or record.get("organizationType") or None),
            naics=record.get("naicsCode"),
            classification_code=record.get("classificationCode"),
            set_aside=record.get("typeOfSetAsideDescription") or record.get("typeOfSetAside"),
            posted_date=record.get("postedDate"),
            response_deadline=record.get("responseDeadLine") or record.get("responseDeadline"),
            description=str(record.get("description") or ""),
            ui_link=record.get("uiLink"),
            attachments=atts,
            raw=record,
        )
