#!/usr/bin/env python3
"""
clause_extractor.py
===================
Extract FAR / DFARS / agency-supplement clause and provision citations from
text (opportunity description, PDF/DOCX text, etc.). Returns a list of unique
citations with normalized clause numbers and, when known, a canonical title.

Supported citation forms it recognizes in the wild:
    FAR 52.204-24, FAR Clause 52.204-24, FAR Provision 52.203-11
    52.212-4 (bare number, when regulation is clear from context)
    DFARS 252.204-7012, DFARS Clause 252.225-7001
    AFARS 5152.209-4000, AFFARS 5352.201-9101, DAFFARS 5352....
    NASA/NFS 1852.223-70, HSAR 3052.204-70, DEAR 952.204-2, VAAR 852.219-71

Ignores false positives like phone numbers, ZIPs, dates, and pure part refs
("FAR Part 12", "FAR Subpart 4.19") which aren't clauses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional


# ------------------------- known clause titles -------------------------
# A curated seed of the highest-frequency FAR/DFARS clauses in DoD supply
# contracts. Titles are canonical (Acquisition.gov). Add more freely; the
# extractor still works on anything cited even if the title is unknown.

KNOWN_TITLES: dict[str, str] = {
    # FAR 52.2xx  (representations & certifications, and general clauses)
    "FAR 52.203-13": "Contractor Code of Business Ethics and Conduct",
    "FAR 52.203-17": "Contractor Employee Whistleblower Rights",
    "FAR 52.203-19": "Prohibition on Requiring Certain Internal Confidentiality Agreements or Statements",
    "FAR 52.204-7":  "System for Award Management",
    "FAR 52.204-10": "Reporting Executive Compensation and First-Tier Subcontract Awards",
    "FAR 52.204-13": "System for Award Management Maintenance",
    "FAR 52.204-16": "Commercial and Government Entity Code Reporting",
    "FAR 52.204-18": "Commercial and Government Entity Code Maintenance",
    "FAR 52.204-19": "Incorporation by Reference of Representations and Certifications",
    "FAR 52.204-21": "Basic Safeguarding of Covered Contractor Information Systems",
    "FAR 52.204-23": "Prohibition on Contracting for Hardware, Software, and Services Developed or Provided by Kaspersky Lab and Other Covered Entities",
    "FAR 52.204-24": "Representation Regarding Certain Telecommunications and Video Surveillance Services or Equipment",
    "FAR 52.204-25": "Prohibition on Contracting for Certain Telecommunications and Video Surveillance Services or Equipment",
    "FAR 52.204-26": "Covered Telecommunications Equipment or Services—Representation",
    "FAR 52.209-6":  "Protecting the Government's Interest When Subcontracting with Contractors Debarred, Suspended, or Proposed for Debarment",
    "FAR 52.209-10": "Prohibition on Contracting with Inverted Domestic Corporations",
    "FAR 52.211-6":  "Brand Name or Equal",
    "FAR 52.211-14": "Notice of Priority Rating for National Defense, Emergency Preparedness, and Energy Program Use",
    "FAR 52.212-1":  "Instructions to Offerors—Commercial Products and Commercial Services",
    "FAR 52.212-3":  "Offeror Representations and Certifications—Commercial Products and Commercial Services",
    "FAR 52.212-4":  "Contract Terms and Conditions—Commercial Products and Commercial Services",
    "FAR 52.212-5":  "Contract Terms and Conditions Required to Implement Statutes or Executive Orders—Commercial Products and Commercial Services",
    "FAR 52.213-4":  "Terms and Conditions—Simplified Acquisitions (Other Than Commercial Products and Commercial Services)",
    "FAR 52.219-1":  "Small Business Program Representations",
    "FAR 52.219-6":  "Notice of Total Small Business Set-Aside",
    "FAR 52.219-8":  "Utilization of Small Business Concerns",
    "FAR 52.219-14": "Limitations on Subcontracting",
    "FAR 52.219-28": "Post-Award Small Business Program Rerepresentation",
    "FAR 52.222-3":  "Convict Labor",
    "FAR 52.222-19": "Child Labor—Cooperation with Authorities and Remedies",
    "FAR 52.222-21": "Prohibition of Segregated Facilities",
    "FAR 52.222-26": "Equal Opportunity",
    "FAR 52.222-35": "Equal Opportunity for Veterans",
    "FAR 52.222-36": "Equal Opportunity for Workers with Disabilities",
    "FAR 52.222-37": "Employment Reports on Veterans",
    "FAR 52.222-40": "Notification of Employee Rights Under the National Labor Relations Act",
    "FAR 52.222-50": "Combating Trafficking in Persons",
    "FAR 52.223-18": "Encouraging Contractor Policies to Ban Text Messaging While Driving",
    "FAR 52.225-1":  "Buy American—Supplies",
    "FAR 52.225-13": "Restrictions on Certain Foreign Purchases",
    "FAR 52.232-33": "Payment by Electronic Funds Transfer—System for Award Management",
    "FAR 52.232-40": "Providing Accelerated Payments to Small Business Subcontractors",
    "FAR 52.233-1":  "Disputes",
    "FAR 52.233-3":  "Protest After Award",
    "FAR 52.233-4":  "Applicable Law for Breach of Contract Claim",
    "FAR 52.243-1":  "Changes—Fixed-Price",
    "FAR 52.246-2":  "Inspection of Supplies—Fixed-Price",
    "FAR 52.247-34": "F.o.b. Destination",
    "FAR 52.249-8":  "Default (Fixed-Price Supply and Service)",

    # DFARS 252.2xx  (DoD-specific)
    "DFARS 252.201-7000": "Contracting Officer's Representative",
    "DFARS 252.203-7000": "Requirements Relating to Compensation of Former DoD Officials",
    "DFARS 252.203-7002": "Requirement to Inform Employees of Whistleblower Rights",
    "DFARS 252.204-7000": "Disclosure of Information",
    "DFARS 252.204-7003": "Control of Government Personnel Work Product",
    "DFARS 252.204-7008": "Compliance with Safeguarding Covered Defense Information Controls",
    "DFARS 252.204-7009": "Limitations on the Use or Disclosure of Third-Party Contractor Reported Cyber Incident Information",
    "DFARS 252.204-7012": "Safeguarding Covered Defense Information and Cyber Incident Reporting",
    "DFARS 252.204-7015": "Notice of Authorized Disclosure of Information for Litigation Support",
    "DFARS 252.204-7016": "Covered Defense Telecommunications Equipment or Services—Representation",
    "DFARS 252.204-7017": "Prohibition on the Acquisition of Covered Defense Telecommunications Equipment or Services—Representation",
    "DFARS 252.204-7018": "Prohibition on the Acquisition of Covered Defense Telecommunications Equipment or Services",
    "DFARS 252.204-7019": "Notice of NIST SP 800-171 DoD Assessment Requirements",
    "DFARS 252.204-7020": "NIST SP 800-171 DoD Assessment Requirements",
    "DFARS 252.204-7021": "Contractor Compliance with the Cybersecurity Maturity Model Certification Level Requirement",
    "DFARS 252.211-7003": "Item Unique Identification and Valuation",
    "DFARS 252.223-7008": "Prohibition of Hexavalent Chromium",
    "DFARS 252.225-7000": "Buy American—Balance of Payments Program Certificate",
    "DFARS 252.225-7001": "Buy American and Balance of Payments Program",
    "DFARS 252.225-7002": "Qualifying Country Sources as Subcontractors",
    "DFARS 252.225-7008": "Restriction on Acquisition of Specialty Metals",
    "DFARS 252.225-7009": "Restriction on Acquisition of Certain Articles Containing Specialty Metals",
    "DFARS 252.225-7012": "Preference for Certain Domestic Commodities",
    "DFARS 252.225-7048": "Export-Controlled Items",
    "DFARS 252.227-7013": "Rights in Technical Data—Noncommercial Items",
    "DFARS 252.227-7015": "Technical Data—Commercial Items",
    "DFARS 252.232-7003": "Electronic Submission of Payment Requests and Receiving Reports",
    "DFARS 252.232-7006": "Wide Area WorkFlow Payment Instructions",
    "DFARS 252.232-7010": "Levies on Contract Payments",
    "DFARS 252.239-7018": "Supply Chain Risk",
    "DFARS 252.243-7001": "Pricing of Contract Modifications",
    "DFARS 252.244-7000": "Subcontracts for Commercial Products or Commercial Services",
    "DFARS 252.246-7003": "Notification of Potential Safety Issues",
    "DFARS 252.246-7008": "Sources of Electronic Parts",
    "DFARS 252.247-7023": "Transportation of Supplies by Sea",
}


# ------------------------- regex library -------------------------

# The core clause number: NNN-N[NNN]  with optional -Alt-N or (a)(1) style tail.
_CLAUSE_NUM = r"\d{3}-\d{1,4}(?:[A-Z]?)"
_CLAUSE_NUM_RE = rf"({_CLAUSE_NUM})"

# Named regulations that live at Part 52-analogous numbering per agency:
#   FAR 52.xxx-xx        (base)
#   DFARS 252.xxx-xxxx   (DoD)
#   AFARS 5152.xxx-xxxx  (Army supplement)
#   AFFARS/DAFFARS 5352.xxx-xxxx  (Air Force supplement)
#   NFS 1852.xxx-xx      (NASA)
#   HSAR 3052.xxx-xx     (DHS)
#   DEAR 952.xxx-xx      (DOE)
#   VAAR 852.xxx-xx      (VA)
_AGENCY_MAP = {
    "FAR":    r"52",
    "DFARS":  r"252",
    "AFARS":  r"5152",
    "AFFARS": r"5352",
    "DAFFARS": r"5352",
    "NFS":    r"1852",
    "HSAR":   r"3052",
    "DEAR":   r"952",
    "VAAR":   r"852",
    "AGAR":   r"452",
    "EDAR":   r"3452",
    "TAR":    r"1052",
}

# Compile once. The lookaround at the tail avoids clipping into decimals/phone digits.
_PATTERNS = []
for reg, part in _AGENCY_MAP.items():
    _PATTERNS.append((
        reg,
        re.compile(
            # optional " Clause"/"Provision" between reg name and number
            rf"\b{reg}(?:\s+(?:Clause|Provision|clause|provision))?\s+"
            rf"({part}\.\d{{3}}-\d{{1,4}}[A-Z]?)"
            r"(?!\d)",
        ),
    ))

# Bare "52.212-4" without a reg name — only accepted when the enclosing text
# elsewhere confirms it's FAR (otherwise we can't tell). Handled below.
_BARE_FAR = re.compile(r"(?<![.\d])(52\.\d{3}-\d{1,4}[A-Z]?)(?!\d)")
_BARE_DFARS = re.compile(r"(?<![.\d])(252\.\d{3}-\d{1,4}[A-Z]?)(?!\d)")


# ------------------------- data class -------------------------

@dataclass
class Clause:
    regulation: str        # "FAR" | "DFARS" | "AFARS" | ...
    number: str            # canonical, e.g. "52.204-24"
    title: Optional[str] = None
    contexts: list[str] = field(default_factory=list)  # short excerpts where it was found

    @property
    def citation(self) -> str:
        return f"{self.regulation} {self.number}"

    @property
    def part(self) -> str:
        """Return the FAR/DFARS 'Part' — e.g. '52.204' or '252.225'."""
        return self.number.rsplit("-", 1)[0]


def _snippet(text: str, m: re.Match, radius: int = 90) -> str:
    start = max(0, m.start() - radius)
    end = min(len(text), m.end() + radius)
    excerpt = text[start:end].replace("\n", " ").replace("\t", " ")
    excerpt = re.sub(r"\s+", " ", excerpt).strip()
    return ("…" if start > 0 else "") + excerpt + ("…" if end < len(text) else "")


def extract_clauses(text: str, *, source: str = "") -> list[Clause]:
    """Return a list of unique Clause objects found in `text`."""
    seen: dict[str, Clause] = {}

    def upsert(reg: str, number: str, m: re.Match):
        citation = f"{reg} {number}"
        c = seen.get(citation)
        if not c:
            c = Clause(regulation=reg, number=number, title=KNOWN_TITLES.get(citation))
            seen[citation] = c
        snip = _snippet(text, m)
        if source:
            snip = f"[{source}] " + snip
        if snip not in c.contexts:
            c.contexts.append(snip)

    # 1) explicit "REG NNN.xxx-xxx" citations
    for reg, pat in _PATTERNS:
        for m in pat.finditer(text):
            upsert(reg, m.group(1), m)

    # 2) bare "52.xxx-xx" / "252.xxx-xxxx" citations (only if reg name is anywhere
    # in the doc — otherwise ambiguous)
    if re.search(r"\bFAR\b", text):
        for m in _BARE_FAR.finditer(text):
            upsert("FAR", m.group(1), m)
    if re.search(r"\bDFARS\b", text):
        for m in _BARE_DFARS.finditer(text):
            upsert("DFARS", m.group(1), m)

    # Sort by (regulation, numeric part, sub-number) for stable output.
    def sort_key(c: Clause):
        try:
            part, sub = c.number.split("-", 1)
            sub_num = int(re.match(r"\d+", sub).group(0))
            sub_suffix = sub[len(str(sub_num)):]
        except Exception:
            part, sub_num, sub_suffix = c.number, 0, ""
        return (c.regulation, part, sub_num, sub_suffix)

    return sorted(seen.values(), key=sort_key)


def clauses_to_dicts(clauses: Iterable[Clause]) -> list[dict]:
    return [
        {
            "regulation": c.regulation,
            "number": c.number,
            "citation": c.citation,
            "part": c.part,
            "title": c.title,
            "contexts": c.contexts,
        }
        for c in clauses
    ]


# ------------------------- CLI -------------------------

if __name__ == "__main__":
    import argparse, json, sys
    from pathlib import Path

    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="path to a .txt file with contract text")
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    clauses = extract_clauses(text, source=Path(args.file).name)
    json.dump(clauses_to_dicts(clauses), sys.stdout, indent=2)
    print(f"\n\n[{len(clauses)} clauses found]", file=sys.stderr)
