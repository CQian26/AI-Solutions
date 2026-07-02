#!/usr/bin/env python3
"""
sample/gen_mocks.py
===================
Generate realistic per-contract mock SAM.gov v2 responses for every parsed
opportunity, so the pipeline demo covers the FULL bidmatch email even without
a live api.data.gov key.

Templates are chosen by (FSC × agency). Real SAM.gov data will look identical
in shape but with the real clause set — swap the mocks out by setting
SAM_API_KEY and re-running run.py.

Usage:
    python3 sample/gen_mocks.py \
        --opportunities sample/opportunities.json \
        --out-dir sample/mock_sam
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


# ------------------------- clause templates -------------------------

# The universal commercial-item core seen on nearly every FAR-based supply
# opportunity. These will drive the top of the Scorecard sheet.
FAR_COMMERCIAL_CORE = [
    "FAR 52.203-13", "FAR 52.203-17",
    "FAR 52.204-7", "FAR 52.204-10", "FAR 52.204-13", "FAR 52.204-16",
    "FAR 52.204-24", "FAR 52.204-25", "FAR 52.204-26",
    "FAR 52.209-6",
    "FAR 52.212-1", "FAR 52.212-3", "FAR 52.212-4", "FAR 52.212-5",
    "FAR 52.222-3", "FAR 52.222-19", "FAR 52.222-21", "FAR 52.222-26",
    "FAR 52.222-35", "FAR 52.222-36", "FAR 52.222-50",
    "FAR 52.225-13",
    "FAR 52.232-33", "FAR 52.232-40",
    "FAR 52.233-1", "FAR 52.233-3", "FAR 52.233-4",
    "FAR 52.243-1", "FAR 52.246-2", "FAR 52.247-34", "FAR 52.249-8",
]
FAR_SMALL_BIZ = ["FAR 52.219-1", "FAR 52.219-6", "FAR 52.219-8", "FAR 52.219-14", "FAR 52.219-28"]
FAR_BUY_AMERICAN = ["FAR 52.225-1"]

# DFARS universally-required for DoD supply.
DFARS_CORE = [
    "DFARS 252.203-7000", "DFARS 252.203-7002",
    "DFARS 252.204-7000", "DFARS 252.204-7003",
    "DFARS 252.204-7012", "DFARS 252.204-7015",
    "DFARS 252.204-7016", "DFARS 252.204-7017", "DFARS 252.204-7018",
    "DFARS 252.204-7019", "DFARS 252.204-7020",
    "DFARS 252.211-7003",
    "DFARS 252.225-7001",
    "DFARS 252.232-7003", "DFARS 252.232-7006", "DFARS 252.232-7010",
    "DFARS 252.243-7001",
    "DFARS 252.244-7000",
    "DFARS 252.247-7023",
]
DFARS_CMMC = ["DFARS 252.204-7021"]
DFARS_TECH_DATA = ["DFARS 252.227-7013", "DFARS 252.227-7015"]
DFARS_METALS = ["DFARS 252.225-7008", "DFARS 252.225-7009"]
DFARS_HEXCHROME = ["DFARS 252.223-7008"]
DFARS_ELECTRONIC_PARTS = ["DFARS 252.246-7003", "DFARS 252.246-7008"]
DFARS_SUPPLY_CHAIN = ["DFARS 252.239-7018"]
DFARS_EXPORT = ["DFARS 252.225-7048"]
DFARS_BUY_AMERICAN = ["DFARS 252.225-7000", "DFARS 252.225-7002", "DFARS 252.225-7012"]

# DLAD is the DLA-specific supplement. Applied to any DLA solicitation.
DLAD_CORE = [
    "DLAD 52.204-9002",
    "DLAD 52.211-9006",     # Time of Delivery — on nearly every DLA supply order
    "DLAD 52.211-9026",
    "DLAD 52.213-9001",
    "DLAD 52.215-9016",     # Technical & Quality
    "DLAD 52.232-9004",
    "DLAD 52.246-9008",     # Inspection & acceptance at origin
    "DLAD 52.246-9012",     # Higher-Level Quality
    "DLAD 52.246-9080",
    "DLAD 52.247-9032",     # Item peculiar packaging
]
DLAD_PVT = ["DLAD 52.211-9034", "DLAD 52.246-9060"]  # Post-award product testing


# ------------------------- MIL-SPEC templates -------------------------

# Weapons / gun parts (FSC 10, small arms)
MIL_WEAPON_PARTS = ["MIL-STD-129P", "MIL-STD-130N", "MIL-STD-1916", "MIL-DTL-13924D", "MIL-PRF-13830B"]
# Vehicle parts (FSC 25)
MIL_VEHICLE_PARTS = ["MIL-STD-129P", "MIL-STD-130N", "MIL-STD-1916", "MIL-STD-810G", "MIL-DTL-31000C"]
# Metals / plates (FSC 95, 34)
MIL_METALS = ["MIL-STD-129P", "MIL-DTL-24594B", "MIL-DTL-46100E", "FED-STD-595C"]
# Marine / ship (FSC 20)
MIL_MARINE = ["MIL-STD-129P", "MIL-STD-130N", "MIL-STD-2003-5", "MIL-STD-1310H"]
# Welding equipment
MIL_WELDING = ["MIL-STD-1595", "MIL-STD-2035", "AWS D1.1"]
# Aviation / precision
MIL_AVIATION = ["MIL-STD-129P", "MIL-STD-130N", "MIL-STD-1553", "AS9100D"]


# ------------------------- templates by profile -------------------------

def _dla_supply_template(fsc: str, is_metal: bool = False, has_electronics: bool = False,
                         export_controlled: bool = False, small_business: bool = False):
    clauses = list(FAR_COMMERCIAL_CORE) + list(DFARS_CORE) + list(DLAD_CORE)
    clauses += FAR_BUY_AMERICAN + DFARS_BUY_AMERICAN
    clauses += DFARS_CMMC + DFARS_TECH_DATA
    if is_metal:
        clauses += DFARS_METALS + DFARS_HEXCHROME
    if has_electronics:
        clauses += DFARS_ELECTRONIC_PARTS + DFARS_SUPPLY_CHAIN
    if export_controlled:
        clauses += DFARS_EXPORT
    if small_business:
        clauses += FAR_SMALL_BIZ

    # MIL-SPECs by FSC
    mils = []
    if fsc.startswith("10"):
        mils = MIL_WEAPON_PARTS
    elif fsc.startswith("25"):
        mils = MIL_VEHICLE_PARTS
    elif fsc.startswith("95"):
        mils = MIL_METALS
    elif fsc.startswith("34"):
        mils = MIL_WELDING + ["MIL-STD-129P"]
    elif fsc.startswith("20"):
        mils = MIL_MARINE
    else:
        mils = ["MIL-STD-129P", "MIL-STD-130N"]

    return {"clauses": clauses, "standards": mils}


def _navy_template(fsc: str):
    base = _dla_supply_template(fsc, is_metal=(fsc.startswith("95")))
    base["clauses"] += [
        "NMCARS 5252.204-9400", "NMCARS 5252.222-9300",
        "NMCARS 5252.223-9400", "NMCARS 5252.242-9115",
    ]
    base["standards"] = list(set(base["standards"]) | set(MIL_MARINE))
    return base


def _nasa_template(fsc: str):
    # NASA: FAR + NFS; DFARS does NOT apply.
    clauses = list(FAR_COMMERCIAL_CORE) + FAR_BUY_AMERICAN
    clauses += [
        "NFS 1852.204-76", "NFS 1852.223-70", "NFS 1852.225-70",
        "NFS 1852.227-11", "NFS 1852.245-70", "NFS 1852.246-70",
    ]
    return {"clauses": clauses, "standards": ["MIL-STD-129P", "MIL-STD-1540E", "MIL-STD-810G", "FED-STD-595C"]}


def _doj_template(fsc: str, small_business: bool = False):
    # FBI / DoJ: FAR + JAR; DFARS does NOT apply.
    clauses = list(FAR_COMMERCIAL_CORE) + FAR_BUY_AMERICAN
    clauses += ["JAR 2852.203-70", "JAR 2852.204-70", "JAR 2852.209-70", "JAR 2852.209-71"]
    if small_business:
        clauses += FAR_SMALL_BIZ
    return {"clauses": clauses, "standards": ["MIL-STD-129P", "MIL-STD-130N"]}


def _dhs_template(fsc: str):
    # DHS: FAR + HSAR; DFARS does NOT apply.
    clauses = list(FAR_COMMERCIAL_CORE) + FAR_BUY_AMERICAN
    clauses += ["HSAR 3052.204-70", "HSAR 3052.204-71", "HSAR 3052.209-70", "HSAR 3052.219-70"]
    return {"clauses": clauses, "standards": ["MIL-STD-129P", "FED-STD-595C"]}


def _ja_template(fsc: str):
    """Justification & Approval notice — a subset of FAR only."""
    return {
        "clauses": ["FAR 52.204-7", "FAR 52.212-4", "FAR 52.212-5", "FAR 52.243-1", "FAR 52.249-8",
                    "DFARS 252.203-7000", "DFARS 252.204-7012"],
        "standards": [],
    }


# ------------------------- profile router -------------------------

def profile_for(op: dict) -> str:
    agency = (op.get("agency") or "").upper()
    title = (op.get("title") or "").upper()
    fsc = op.get("fsc") or ""
    raw = (op.get("raw") or "").upper()

    if "MARYLAND" in agency or "METRA" in agency or "FORT WORTH" in agency or " - " in (op.get("raw") or "")[:30]:
        # State / local portals — not on SAM.gov.
        return "off_sam"
    if "JUSTICE" in agency or "FBI" in agency:
        return "doj"
    if "HOMELAND" in agency or "DHS" in agency:
        return "dhs"
    if "NATIONAL AERONAUTICS" in agency or "NASA" in agency:
        return "nasa"
    if "USNS" in title or "NAVY" in agency:
        return "navy"
    if "J&A" in title:
        return "j&a"
    return "dla"


def build_mock(op: dict) -> dict | None:
    """Return a SAM.gov v2-shaped mock response for `op`, or None if it should
    be skipped (state/local portals, obvious duplicates, etc.)."""
    prof = profile_for(op)
    if prof == "off_sam":
        return None

    fsc = op.get("fsc") or ""
    small_business = (
        "SMALL BUSINESS" in (op.get("raw") or "").upper()
        or "FBI" in (op.get("agency") or "").upper()
    )
    is_metal = fsc.startswith(("95", "9515", "9535"))
    has_electronics = any(k in (op.get("title") or "").upper()
                          for k in ("SWITCH", "SEAR", "PIN,FIRING", "SPRING"))
    export_controlled = fsc.startswith("10") or "AMMO" in (op.get("title") or "").upper() \
                        or "SUPPRESSOR" in (op.get("title") or "").upper()

    if prof == "navy":
        tpl = _navy_template(fsc)
    elif prof == "nasa":
        tpl = _nasa_template(fsc)
    elif prof == "doj":
        tpl = _doj_template(fsc, small_business=small_business)
    elif prof == "dhs":
        tpl = _dhs_template(fsc)
    elif prof == "j&a":
        tpl = _ja_template(fsc)
    else:  # dla
        tpl = _dla_supply_template(
            fsc,
            is_metal=is_metal,
            has_electronics=has_electronics,
            export_controlled=export_controlled,
            small_business=small_business,
        )

    # Build the description text that the clause extractor will actually scan.
    # (This mimics the "clause list" section a real SAM.gov opportunity carries.)
    clause_text = "; ".join(tpl["clauses"])
    std_text = ", ".join(tpl["standards"])
    description = (
        f"[DEMO MOCK — replace with live SAM.gov API by setting SAM_API_KEY.] "
        f"Solicitation for {op.get('title') or op.get('raw')}. "
        f"Agency: {op.get('agency')}. "
        f"The following provisions and clauses apply: {clause_text}. "
        + (f"Technical requirements: {std_text}. " if std_text else "")
    )

    sol_num = op.get("solicitation") or _synth_sol(op)
    notice_id = f"mock-{sol_num}" if sol_num else f"mock-{_sanitize(op.get('title') or op.get('raw'))}"

    return {
        "totalRecords": 1,
        "opportunitiesData": [{
            "noticeId": notice_id,
            "solicitationNumber": sol_num,
            "title": op.get("title") or "",
            "fullParentPathName": op.get("agency") or "",
            "naicsCode": _naics_for(fsc),
            "classificationCode": fsc,
            "typeOfSetAsideDescription": "Total Small Business Set-Aside" if small_business else None,
            "postedDate": "2026-05-15",
            "responseDeadLine": _iso_due(op.get("due_date")),
            "uiLink": f"https://sam.gov/opp/{notice_id}/view",
            "resourceLinks": [],
            "description": description,
        }]
    }


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s or "").strip("_")


def _synth_sol(op: dict) -> str | None:
    """When a bidmatch line has no SOL#, synthesize a stable pseudo-key from its title."""
    t = op.get("title") or ""
    if not t:
        return None
    return "MOCK-" + _sanitize(t)[:40]


def _naics_for(fsc: str) -> str | None:
    """Best-guess NAICS from FSC — good enough for a demo scorecard."""
    if not fsc:
        return None
    two = fsc[:2]
    return {
        "10": "332994",  # small arms & ammo mfg
        "20": "336611",  # ship building & repair
        "24": "336999",  # transportation equip
        "25": "336413",  # aerospace/vehicle parts
        "34": "333992",  # welding & soldering equip
        "36": "333249",  # laser cutter / industrial machinery
        "95": "331110",  # steel manufacturing
    }.get(two)


def _iso_due(mmddyy: str | None) -> str | None:
    if not mmddyy:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", mmddyy)
    if not m:
        return None
    mm, dd, yy = m.group(1), m.group(2), m.group(3)
    if len(yy) == 2:
        yy = "20" + yy
    return f"{yy}-{int(mm):02d}-{int(dd):02d}T17:00:00-05:00"


# ------------------------- CLI -------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--opportunities", default="sample/opportunities.json")
    ap.add_argument("--out-dir", default="sample/mock_sam")
    args = ap.parse_args()

    ops = json.loads(Path(args.opportunities).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n_out, n_skip = 0, 0
    for op in ops:
        mock = build_mock(op)
        if mock is None:
            n_skip += 1
            continue
        # Same sanitizer as sam_client._resolve_mock:
        key = op.get("solicitation") or op.get("title") or ""
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
        if not safe:
            n_skip += 1
            continue
        (out_dir / f"search_{safe}.json").write_text(json.dumps(mock, indent=2), encoding="utf-8")
        n_out += 1

    print(f"Wrote {n_out} mocks to {out_dir}; skipped {n_skip} (off-SAM/untitled).")


if __name__ == "__main__":
    main()
