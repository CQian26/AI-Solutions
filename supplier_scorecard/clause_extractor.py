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


# ------------------------- plain-English explanations -------------------------
# One sentence per clause describing what the supplier actually has to DO to
# comply. Kept intentionally practical — not a legal summary, a "what does this
# mean for my capability profile?" note.

EXPLANATIONS: dict[str, str] = {
    # ----- FAR admin & reps/certs (mostly boilerplate; kept for completeness) -----
    "FAR 52.203-13": "Maintain a written business-ethics code and an internal compliance/awareness program.",
    "FAR 52.203-17": "Post whistleblower-rights notices for your employees.",
    "FAR 52.203-19": "Don't require employee confidentiality agreements that would block reporting fraud.",
    "FAR 52.204-7":  "Must have an active SAM registration before award.",
    "FAR 52.204-10": "Report executive compensation if your annual federal revenue exceeds $30M.",
    "FAR 52.204-13": "Keep your SAM registration active throughout performance.",
    "FAR 52.204-16": "You must have (and report) a CAGE code.",
    "FAR 52.204-18": "Keep the CAGE code current on ownership or control changes.",
    "FAR 52.204-19": "SAM reps & certs are the record of truth; keep them accurate.",
    "FAR 52.204-21": "Implement the 15 basic cybersecurity safeguards on any system handling Federal Contract Information (FCI).",
    "FAR 52.204-23": "No Kaspersky Lab hardware, software, or services in your supply chain.",
    "FAR 52.204-24": "Represent whether you use covered Chinese telecom (Huawei, ZTE, Hytera, Hikvision, Dahua).",
    "FAR 52.204-25": "Section 889 — you cannot provide the government any covered Chinese telecom equipment/services.",
    "FAR 52.204-26": "Additional covered-telecom representation flow-down.",
    "FAR 52.209-6":  "Screen subcontractors against SAM.gov Exclusions before award; notify CO if you propose a debarred party.",
    "FAR 52.209-10": "Certify you are not an inverted domestic corporation (former US company reincorporated abroad).",
    "FAR 52.211-6":  "'Brand name or equal' — if the spec names a brand, your alternate must meet the listed salient characteristics.",
    "FAR 52.211-14": "DPAS priority rating (DO / DX) — you must schedule this order ahead of unrated work.",
    "FAR 52.212-1":  "Standard offer instructions for commercial-item solicitations (how and when to submit your bid).",
    "FAR 52.212-3":  "Consolidated annual reps & certs — submit once per year via SAM.",
    "FAR 52.212-4":  "The standard commercial-item contract terms: payment, inspection, warranty, disputes.",
    "FAR 52.212-5":  "The list of statutorily required clauses that also apply to commercial contracts (EEO, small-business, labor, etc.).",
    "FAR 52.213-4":  "Terms for simplified acquisitions of other-than-commercial items.",
    "FAR 52.219-1":  "Represent your small-business size status for the contract's NAICS.",
    "FAR 52.219-6":  "This procurement is set aside for small business — only SBs may bid.",
    "FAR 52.219-8":  "Give small business and socioeconomic categories equitable subcontract opportunity.",
    "FAR 52.219-14": "As a set-aside prime, you must perform at least 50% of the cost of manufacture with your own employees (supply contracts).",
    "FAR 52.219-28": "Re-represent your small-business size if you outgrow the standard during performance.",
    "FAR 52.222-3":  "No convict labor in performance.",
    "FAR 52.222-19": "Comply with EO 13126 child-labor rules; sign the required certification.",
    "FAR 52.222-21": "No segregated facilities.",
    "FAR 52.222-26": "Equal-employment-opportunity — no discrimination by race, color, religion, sex, or national origin.",
    "FAR 52.222-35": "Affirmative action for protected veterans (contracts >$150K).",
    "FAR 52.222-36": "Affirmative action for workers with disabilities (contracts >$15K).",
    "FAR 52.222-37": "File the annual VETS-4212 report on veteran employment.",
    "FAR 52.222-40": "Post the NLRA employee-rights notice at your worksites.",
    "FAR 52.222-50": "Zero-tolerance policy on human trafficking; specific prohibitions on your employees.",
    "FAR 52.223-18": "Maintain a written policy banning texting while driving on government business.",
    "FAR 52.225-1":  "Buy American Act — supply end items must be domestic (or from a qualifying country).",
    "FAR 52.225-13": "Restriction on Certain Foreign Purchases — no items from OFAC-sanctioned countries.",
    "FAR 52.232-33": "Payments only by EFT to the account in your SAM registration.",
    "FAR 52.232-40": "You must pay your small-business subcontractors within 15 days.",
    "FAR 52.233-1":  "Contract disputes are handled under the Contract Disputes Act (appeals to the Boards).",
    "FAR 52.233-3":  "Post-award protest may pause performance while it's resolved.",
    "FAR 52.233-4":  "US federal law governs any breach-of-contract claim.",
    "FAR 52.243-1":  "Changes clause — the CO may unilaterally change certain contract terms; you get an equitable adjustment.",
    "FAR 52.246-2":  "Inspection of Supplies — you're responsible for QC; government inspects at destination (or origin if specified).",
    "FAR 52.247-34": "F.o.b. Destination — you own the freight cost and risk of loss until delivery.",
    "FAR 52.249-8":  "Default clause — government may terminate for cause; you may be liable for excess reprocurement costs.",

    # ----- DFARS -----
    "DFARS 252.201-7000": "You'll deal with a designated Contracting Officer's Representative (COR).",
    "DFARS 252.203-7000": "'Golden parachute' rule — restrictions on compensating former DoD officials.",
    "DFARS 252.203-7002": "Inform employees of DoD-specific whistleblower rights and reporting channels.",
    "DFARS 252.204-7000": "Don't release contract information publicly without CO authorization.",
    "DFARS 252.204-7003": "Government retains rights in work product from DoD personnel.",
    "DFARS 252.204-7008": "Certify you can meet the safeguarding requirements of DFARS 252.204-7012.",
    "DFARS 252.204-7009": "Limits on how you can use/disclose third-party cyber-incident info shared with you.",
    "DFARS 252.204-7012": "Safeguard Covered Defense Information per NIST SP 800-171; report cyber incidents to DoD within 72 hours.",
    "DFARS 252.204-7015": "Disclose specified info in litigation support tasks.",
    "DFARS 252.204-7016": "Representation about covered defense telecom equipment/services.",
    "DFARS 252.204-7017": "Representation about acquiring covered defense telecom equipment/services.",
    "DFARS 252.204-7018": "You may not acquire or provide covered defense telecom equipment for DoD.",
    "DFARS 252.204-7019": "You must have posted a NIST SP 800-171 self-assessment score in SPRS before award.",
    "DFARS 252.204-7020": "You must provide NIST 800-171 assessment info on request; DoD may conduct Medium/High assessments.",
    "DFARS 252.204-7021": "You must hold the required CMMC certification level before award.",
    "DFARS 252.211-7003": "Mark deliverables with an Item Unique Identifier (IUID) per MIL-STD-130; register in the DoD IUID registry.",
    "DFARS 252.223-7008": "No hexavalent chromium in deliverables unless the CO approves in writing.",
    "DFARS 252.225-7000": "Certify country of origin for supplies (Buy American / Balance of Payments certificate).",
    "DFARS 252.225-7001": "Provide only domestic or qualifying-country end products (DoD Buy American).",
    "DFARS 252.225-7002": "Your subcontractors must also source from the US or qualifying countries.",
    "DFARS 252.225-7008": "Use only domestically melted or produced specialty metals unless an exception applies.",
    "DFARS 252.225-7009": "Restriction on foreign specialty metals in items with specialty-metal content.",
    "DFARS 252.225-7012": "Certain commodities (food, hand tools, some chemicals) must be domestic — 'Preference for Certain Domestic Commodities.'",
    "DFARS 252.225-7048": "Comply with ITAR/EAR export-control laws; obtain any required licenses.",
    "DFARS 252.227-7013": "Government gets a license to noncommercial technical data you deliver.",
    "DFARS 252.227-7015": "Government gets a license to commercial technical data you deliver.",
    "DFARS 252.232-7003": "Submit payment requests and receiving reports electronically via WAWF (iRAPT).",
    "DFARS 252.232-7006": "WAWF-specific routing codes for your invoice (Pay DoDAAC etc.).",
    "DFARS 252.232-7010": "IRS may levy payments on this contract to satisfy your federal tax debts.",
    "DFARS 252.239-7018": "Assess and mitigate supply-chain risk (SCRM) across your supplier tiers.",
    "DFARS 252.243-7001": "Contract modifications are priced per Truth in Negotiations Act (cost or pricing data).",
    "DFARS 252.244-7000": "Flow the same requirements down to your commercial-item subcontractors.",
    "DFARS 252.246-7003": "Notify DoD promptly of any safety issue discovered with a delivered item.",
    "DFARS 252.246-7008": "Source electronic parts from OEMs, franchised dealers, or trusted suppliers — no unauthorized brokers.",
    "DFARS 252.247-7023": "Ocean shipments must move on US-flag vessels to the extent available.",

    # ----- DLAD (DLA-specific supply supplement) -----
    "DLAD 52.204-9002": "Handling and disclosure rules for contractor-provided info at DLA.",
    "DLAD 52.209-9010": "Rules for handling and returning free-issue government material.",
    "DLAD 52.211-9006": "DLA delivery schedule — dictates required leadtime from contract award to shipment.",
    "DLAD 52.211-9026": "Reserved contract-information handling procedures.",
    "DLAD 52.211-9034": "Post-award product testing — DLA may test your delivered items and cancel/reject on failure.",
    "DLAD 52.213-9001": "Notice about the procedure for filling certain solicitation requirements.",
    "DLAD 52.215-9016": "Higher-level technical and quality requirements may apply (e.g., ISO 9001, AS9100).",
    "DLAD 52.215-9023": "You may be required to participate in DLA reverse-auction bidding.",
    "DLAD 52.223-9001": "Apply hazard-warning labels on hazardous items per DoT/OSHA.",
    "DLAD 52.232-9004": "Standard DLA payment instructions (specific routing).",
    "DLAD 52.233-9002": "Alternative dispute resolution encouraged for DLA disputes.",
    "DLAD 52.246-9008": "Inspection and acceptance is at origin (source inspection at your facility).",
    "DLAD 52.246-9012": "Implement a higher-level quality management system (typically ISO 9001 or AS9100).",
    "DLAD 52.246-9060": "Product Verification Testing under DLA's Automated Best Value System.",
    "DLAD 52.246-9080": "DLA-managed items have specific quality-system requirements you must meet.",
    "DLAD 52.247-9032": "Item-peculiar packaging and marking — DLA-specific packing/marking specs.",
    "DLAD 52.247-9059": "Ship-in-place — items stay at your facility until DLA calls them forward.",

    # ----- Agency supplements -----
    "NMCARS 5252.204-9400": "Navy contractor access to federally-controlled facilities.",
    "NMCARS 5252.222-9300": "Navy contractor personnel access to Navy work sites.",
    "NMCARS 5252.223-9400": "Report accidents/incidents to the Navy per this clause.",
    "NMCARS 5252.227-9113": "Navy-specific rights in technical data.",
    "NMCARS 5252.242-9115": "Comply with technical instructions issued during performance.",

    "JAR 2852.203-70":  "DoJ whistleblower protection notice/training.",
    "JAR 2852.204-70":  "DoJ contractor employees must undergo security screening.",
    "JAR 2852.209-70":  "Disclose any organizational conflicts of interest (OCI).",
    "JAR 2852.209-71":  "DoJ standards of conduct — apply to your on-site employees.",

    "HHSAR 352.203-70": "Anti-lobbying restrictions (HHS-specific).",
    "HHSAR 352.222-70": "Cooperate in EEO investigations.",
    "HHSAR 352.224-70": "Privacy Act requirements when handling personally-identifiable information.",

    "HSAR 3052.204-70": "DHS IT-resources security requirements.",
    "HSAR 3052.204-71": "DHS contractor-employee access clearance.",
    "HSAR 3052.209-70": "No inverted-corporation contracts (DHS variant).",
    "HSAR 3052.219-70": "Small-business subcontracting-plan reporting for DHS.",

    "NFS 1852.204-76": "NASA IT-security requirements for unclassified systems.",
    "NFS 1852.223-70": "Maintain a written Safety and Health plan.",
    "NFS 1852.225-70": "Handle export licensing for items subject to ITAR/EAR.",
    "NFS 1852.227-11": "Contractor may retain title to inventions (Bayh-Dole implementation).",
    "NFS 1852.245-70": "Rules for requesting and using NASA-owned equipment.",
    "NFS 1852.246-70": "Mission-critical space system personnel reliability program.",
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


# ------------------- broader regulatory / statutory citation classes -------
# These do NOT ship with per-citation title/explanation lookup tables — we
# catch the CATEGORY (any CFR / USC / EO / DoDI / PubL reference) and let
# the scorecard show the raw citation. Users looking to attach meaning to
# a specific one can add it to KNOWN_TITLES + EXPLANATIONS.

# 22 CFR 120, 13 CFR 121.201, 22 CFR 120-130
_CFR_RE = re.compile(r"\b(\d{1,2})\s+CFR\s+(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)\b")
# 10 USC 4862, 41 U.S.C. § 8302 (case-insensitive; optional dots and §)
_USC_RE = re.compile(r"\b(\d{1,2})\s+U\.?S\.?C\.?\s+(?:§\s*)?(\d{3,5}[a-z]?)\b", re.I)
# EO 14028, Executive Order 14028, E.O. 14028
_EO_RE  = re.compile(r"\b(?:Executive Order|E\.O\.|EO)\s+(\d{4,5})\b")
# DoDI 5000.02, DoDM 5220.22-M, DoDD 5205.02
_DODI_RE = re.compile(r"\b(DoD[IMD])\s+(\d{4,5}(?:\.\d+)?(?:-[A-Z])?)\b")
# Pub. L. 116-92, Public Law 118-31
_PUBL_RE = re.compile(r"\b(?:Public\s+Law|Pub\.?\s*L\.?)\s+(\d{2,3}-\d{1,4})\b", re.I)


# ------------------- extra industry-standard classes -----------------------
# Same rule as above: categories only, no per-standard lookup.

_AS_RE      = re.compile(r"\bAS\s?(\d{4,5}[A-Z]?)\b")            # AS9100D, AS9145
_ISO_RE     = re.compile(r"\bISO(?:/IEC)?\s?(\d{4,5}(?::\d{4})?)\b")  # ISO 9001, ISO/IEC 27001:2013
_IPC_RE     = re.compile(r"\bIPC-([A-Z])-(\d+[A-Z]?)\b")         # IPC-A-610H
_AWS_RE     = re.compile(r"\bAWS\s+([A-Z]\d+(?:\.\d+)+[A-Z]?)\b")  # AWS D1.1 (requires a dot)
_ASTM_RE    = re.compile(r"\bASTM\s+([A-Z]\d+[A-Z]?(?:-\d+)?)\b")  # ASTM E8, ASTM A36-16
_ASME_RE    = re.compile(r"\bASME\s+([A-Z]?\d+(?:\.\d+)+[A-Z]?)\b")  # ASME B31.3 (requires a dot)
_IEEE_RE    = re.compile(r"\bIEEE\s+(\d{3,4}(?:\.\d+)?)\b")      # IEEE 1584, IEEE 802.3
_SAE_RE     = re.compile(r"\bSAE\s+(AS|AMS|J|ARP)\s?(\d{3,5}[A-Z]?)\b")  # SAE AMS4928, SAE J429
_NADCAP_RE  = re.compile(r"\bNadcap\s+AC(\d{4,5})\b", re.I)      # Nadcap AC7108
_NIST_SP_RE = re.compile(r"\bNIST\s+SP\s+(800-\d{1,3}[A-Z]?(?:\s*Rev\.?\s*\d+)?)\b", re.I)
_CMMC_RE    = re.compile(r"\bCMMC\s+(?:Level\s+)?L?([1-5])\b", re.I)


# ------------------------- data class -------------------------

@dataclass
class Clause:
    regulation: str        # "FAR" | "DFARS" | "AFARS" | ...
    number: str            # canonical, e.g. "52.204-24"
    title: Optional[str] = None
    explanation: Optional[str] = None  # plain-English "what this means for a supplier"
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
            c = Clause(
                regulation=reg,
                number=number,
                title=KNOWN_TITLES.get(citation),
                explanation=EXPLANATIONS.get(citation),
            )
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

    # 4) Broader regulatory / statutory citation classes.
    #    Number storage: dotted form for CFR/USC so the citation cell reads
    #    naturally ("CFR 22.120") and the sort-key logic keeps working.
    for m in _CFR_RE.finditer(text):
        upsert("CFR", f"{m.group(1)}.{m.group(2)}", m)
    for m in _USC_RE.finditer(text):
        upsert("USC", f"{m.group(1)}.{m.group(2)}", m)
    for m in _EO_RE.finditer(text):
        upsert("EO", m.group(1), m)
    for m in _DODI_RE.finditer(text):
        upsert(m.group(1), m.group(2), m)   # regulation = "DoDI" / "DoDM" / "DoDD"
    for m in _PUBL_RE.finditer(text):
        upsert("PubL", m.group(1), m)

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

    # Additional industry-standard citation classes.
    for m in _AS_RE.finditer(text):
        upsert("AS", m.group(1), m)
    for m in _ISO_RE.finditer(text):
        upsert("ISO", m.group(1), m)
    for m in _IPC_RE.finditer(text):
        upsert(f"IPC-{m.group(1)}", m.group(2), m)
    for m in _AWS_RE.finditer(text):
        upsert("AWS", m.group(1), m)
    for m in _ASTM_RE.finditer(text):
        upsert("ASTM", m.group(1), m)
    for m in _ASME_RE.finditer(text):
        upsert("ASME", m.group(1), m)
    for m in _IEEE_RE.finditer(text):
        upsert("IEEE", m.group(1), m)
    for m in _SAE_RE.finditer(text):
        upsert("SAE", f"{m.group(1)}{m.group(2)}", m)
    for m in _NADCAP_RE.finditer(text):
        upsert("Nadcap", f"AC{m.group(1)}", m)
    for m in _NIST_SP_RE.finditer(text):
        upsert("NIST-SP", m.group(1).strip(), m)
    for m in _CMMC_RE.finditer(text):
        upsert("CMMC", f"L{m.group(1)}", m)

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
            "explanation": c.explanation,
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
