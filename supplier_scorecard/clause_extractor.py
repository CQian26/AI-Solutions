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

    # DLAD 52.xxx-9xxx  (DLA supply supplement — heavily used by DLA solicitations)
    "DLAD 52.204-9002": "Disclosure of Contractor Information",
    "DLAD 52.209-9010": "Rules for Providing Free Issue Property/Material",
    "DLAD 52.211-9006": "Time of Delivery",
    "DLAD 52.211-9026": "Reserved Contract Information",
    "DLAD 52.211-9034": "Post Award Product Testing",
    "DLAD 52.213-9001": "Notice for Filling of Certain Solicitation Requirements",
    "DLAD 52.215-9016": "Technical and Quality Requirements",
    "DLAD 52.215-9023": "Reverse Auction",
    "DLAD 52.223-9001": "Hazard Warning Labels",
    "DLAD 52.232-9004": "Standard Payment Instructions",
    "DLAD 52.233-9002": "Alternative Dispute Resolution",
    "DLAD 52.246-9008": "Inspection and Acceptance at Origin",
    "DLAD 52.246-9012": "Higher-Level Contract Quality Requirements",
    "DLAD 52.246-9060": "Product Verification Testing (PVT) — Automated Best Value System",
    "DLAD 52.246-9080": "Quality System — DLA-Managed Items",
    "DLAD 52.247-9032": "Item Peculiar Packaging and Marking",
    "DLAD 52.247-9059": "Ship-in-Place Instructions",

    # NMCARS 5252.xxx-xxxx  (Navy / Marine Corps supplement)
    "NMCARS 5252.204-9400": "Contractor Access to Federally-Controlled Facilities",
    "NMCARS 5252.222-9300": "Contractor Personnel Access to Work Sites",
    "NMCARS 5252.223-9400": "Accident Reporting and Investigation",
    "NMCARS 5252.227-9113": "Rights in Technical Data (Navy)",
    "NMCARS 5252.242-9115": "Technical Instructions",

    # JAR 2852.xxx-xx  (DoJ supplement — used by FBI and other DoJ components)
    "JAR 2852.203-70": "Whistleblower Protections",
    "JAR 2852.204-70": "Contractor Employee Security Screening",
    "JAR 2852.209-70": "Organizational Conflicts of Interest Notification",
    "JAR 2852.209-71": "Standards of Conduct",

    # HHSAR 352.xxx-xx  (Health & Human Services supplement)
    "HHSAR 352.203-70": "Anti-Lobbying",
    "HHSAR 352.222-70": "Contractor Cooperation in Equal Employment Opportunity Investigations",
    "HHSAR 352.224-70": "Privacy Act",

    # HSAR 3052.xxx-xx  (Dept. of Homeland Security supplement)
    "HSAR 3052.204-70": "Security Requirements for Unclassified Information Technology Resources",
    "HSAR 3052.204-71": "Contractor Employee Access",
    "HSAR 3052.209-70": "Prohibition on Contracts with Corporate Expatriates",
    "HSAR 3052.219-70": "Small Business Subcontracting Plan Reporting",

    # NFS 1852.xxx-xx  (NASA FAR Supplement)
    "NFS 1852.204-76": "Security Requirements for Unclassified Information Technology Resources",
    "NFS 1852.223-70": "Safety and Health",
    "NFS 1852.225-70": "Export Licenses",
    "NFS 1852.227-11": "Patent Rights — Ownership by the Contractor",
    "NFS 1852.245-70": "Contractor Requests for Government-Owned Equipment",
    "NFS 1852.246-70": "Mission Critical Space System Personnel Reliability Program",
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
    "NMCARS": r"5252",   # Navy/Marine Corps supplement
    "NFS":    r"1852",   # NASA
    "HSAR":   r"3052",   # DHS
    "DEAR":   r"952",    # DOE
    "VAAR":   r"852",    # VA
    "AGAR":   r"452",    # USDA
    "EDAR":   r"3452",   # Dept. of Education
    "TAR":    r"1052",   # Treasury
    "HHSAR":  r"352",    # HHS
    "JAR":    r"2852",   # Dept. of Justice
    "GSAR":   r"552",    # GSA
    "EPAAR":  r"1552",   # EPA
    "AIDAR":  r"752",    # USAID
    "DOSAR":  r"652",    # State Department
    "DOLAR":  r"2952",   # Dept. of Labor
    "IAAR":   r"1452",   # Interior
    # DLAD uses the same "52." root as FAR but with 9000-series sub-numbers.
    # Handled with its own regex block below.
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

# DLAD supplements FAR Part 52 with sub-numbers in the 9000-series.
# "DLAD 52.211-9006", "DLAD Clause 52.246-9012", etc.
_DLAD_EXPLICIT = re.compile(
    r"\bDLAD(?:\s+(?:Clause|Provision|clause|provision))?\s+"
    r"(52\.\d{3}-9\d{3}[A-Z]?)(?!\d)"
)
# Any bare 52.NNN-9NNN is treated as DLAD (FAR sub-numbers never reach 9000).
_BARE_DLAD = re.compile(r"(?<![.\d])(52\.\d{3}-9\d{3}[A-Z]?)(?!\d)")

# MIL-SPEC / MIL-STD / FED-STD technical standards.
# Not legally binding "clauses" in the FAR sense, but they ARE binding
# requirements for a supplier and belong on the scorecard.
_MIL_RE = re.compile(
    r"\bMIL-(STD|PRF|DTL|HDBK|SPEC|A|C|F|H|I|P|R|S|T|V|W)-"
    r"([A-Z0-9]+(?:[-/][A-Z0-9]+){0,3}[A-Z]?)\b"
)
_FED_STD_RE = re.compile(r"\bFED-(STD|SPEC)-([A-Z0-9]+(?:-[A-Z0-9]+){0,3})\b")
# Also pick up A-A-NNNNN commercial-item description numbers (e.g. A-A-59126).
_AA_RE = re.compile(r"\b(A-A-\d{2,6}[A-Z]?)\b")


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

    # 1) DLAD (explicit prefix). Do this BEFORE bare-FAR so its 9xxx sub-numbers
    #    don't get miscategorised as FAR by the fallback pass below.
    dlad_hits: set[str] = set()
    for m in _DLAD_EXPLICIT.finditer(text):
        upsert("DLAD", m.group(1), m); dlad_hits.add(m.group(1))
    # Any bare 52.xxx-9xxx (FAR's numbering never reaches 9000) → DLAD.
    for m in _BARE_DLAD.finditer(text):
        upsert("DLAD", m.group(1), m); dlad_hits.add(m.group(1))

    # 2) All other explicit "REG NNN.xxx-xxx" citations.
    for reg, pat in _PATTERNS:
        for m in pat.finditer(text):
            upsert(reg, m.group(1), m)

    # 3) bare "52.xxx-xx" / "252.xxx-xxxx" (only when reg name is somewhere in
    #    the doc, otherwise ambiguous). Skip anything that was already claimed
    #    as DLAD in step 1.
    if re.search(r"\bFAR\b", text):
        for m in _BARE_FAR.finditer(text):
            if m.group(1) in dlad_hits:
                continue
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


# ------------------------- MIL-SPEC / FED-STD harvesting -------------------------

@dataclass
class Standard:
    """A technical standard referenced in the contract (MIL-STD, FED-STD, A-A-...)."""
    kind: str        # e.g. "MIL-STD", "MIL-PRF", "FED-STD", "A-A"
    number: str      # e.g. "810G", "129P", "59126"
    contexts: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        if self.kind == "A-A":
            return f"A-A-{self.number}"
        return f"{self.kind}-{self.number}"


def extract_standards(text: str, *, source: str = "") -> list[Standard]:
    """Return unique Standard citations (MIL-STD, MIL-PRF, FED-STD, A-A-NNNNN)."""
    seen: dict[str, Standard] = {}

    def upsert(kind: str, number: str, m: re.Match):
        key = f"{kind}-{number}"
        s = seen.get(key)
        if not s:
            s = Standard(kind=kind, number=number)
            seen[key] = s
        snip = _snippet(text, m)
        if source:
            snip = f"[{source}] " + snip
        if snip not in s.contexts:
            s.contexts.append(snip)

    for m in _MIL_RE.finditer(text):
        upsert(f"MIL-{m.group(1)}", m.group(2), m)
    for m in _FED_STD_RE.finditer(text):
        upsert(f"FED-{m.group(1)}", m.group(2), m)
    for m in _AA_RE.finditer(text):
        # strip the "A-A-" prefix for storage; citation reassembles it.
        upsert("A-A", m.group(1).removeprefix("A-A-"), m)

    return sorted(seen.values(), key=lambda s: (s.kind, s.number))


def standards_to_dicts(stds: Iterable[Standard]) -> list[dict]:
    return [{"kind": s.kind, "number": s.number, "citation": s.citation, "contexts": s.contexts} for s in stds]


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
