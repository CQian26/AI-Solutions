import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import List

from bs4 import BeautifulSoup

DOC_RE = re.compile(r"doc=([^&\s\"'<>]+)")

_DATE_FORMATS = (
    "%A, %b %d, %Y",   # Tuesday, Jun 16, 2026   (live portal format)
    "%A, %B %d, %Y",   # Tuesday, June 16, 2026
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%m/%d/%y",
)


@dataclass(frozen=True)
class IndexEntry:
    date: date
    doc: str
    article_count: int


def _parse_date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_index(html: str) -> List[IndexEntry]:
    """Parse the index page.

    Live portal layout (verified against capture):
      <tr>
        <td><a href="/go?doc=GUID">Tuesday, Jun 16, 2026</a></td>
        <td>57</td>
        <td>New|<timestamp></td>
      </tr>
    """
    soup = BeautifulSoup(html, "html.parser")
    out: List[IndexEntry] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        link = cells[0].find("a", href=True)
        if not link:
            continue
        m = DOC_RE.search(link["href"])
        if not m:
            continue
        d = _parse_date(link.get_text(strip=True))
        if d is None:
            continue
        try:
            count = int(cells[1].get_text(strip=True))
        except ValueError:
            count = 0
        out.append(IndexEntry(date=d, doc=m.group(1), article_count=count))
    return out
