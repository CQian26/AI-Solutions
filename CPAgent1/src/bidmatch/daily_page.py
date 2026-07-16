from dataclasses import dataclass
from typing import List
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class OpportunityStub:
    doc: str
    seq: int
    source_code: str
    agency: str
    fsc_group: str
    title: str
    matched_keywords: str


def parse_daily(html: str, doc: str) -> List[OpportunityStub]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[OpportunityStub] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        try:
            seq = int(cells[0].get_text(strip=True))
        except ValueError:
            continue
        source = cells[1].get_text(strip=True).lower()
        agency = cells[2].get_text(strip=True)
        fsc = cells[3].get_text(strip=True)
        link = cells[4].find("a")
        title = link.get_text(strip=True) if link else cells[4].get_text(strip=True)
        matched = cells[5].get_text(strip=True)
        out.append(OpportunityStub(
            doc=doc,
            seq=seq,
            source_code=source,
            agency=agency,
            fsc_group=fsc,
            title=title,
            matched_keywords=matched,
        ))
    return out
