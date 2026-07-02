#!/usr/bin/env python3
"""
scorecard_writer.py
===================
Assemble a supplier-scorecard workbook from a list of per-contract results.

Emits an .xlsx with THREE sheets:

  1. "Scorecard"    — one row per unique FAR/DFARS clause across all contracts:
       citation, regulation, part, title, count, coverage%, contract IDs
       Sorted by count (desc). This is the primary "what codes matter" view.

  2. "Contract x Clause" — pivot matrix: rows = clauses, columns = contracts,
       cell = "✓" if that clause appears in that contract, else blank.
       Column headers freeze; first column freezes.

  3. "Raw Contracts" — one row per contract, showing metadata pulled from
       SAM.gov + which sources we scanned (description, N attachments) +
       count of clauses found.

Consumes the shape produced by run.py's pipeline:
    {
      "contracts": [
         {
           "id":        "SPE7L026T0325",        # solicitation# (or notice ID)
           "title":     "...",
           "agency":    "...",
           "naics":     "336413",
           "psc":       "2590",
           "set_aside": "SBA",
           "posted":    "2026-06-05",
           "deadline":  "2026-06-22",
           "url":       "https://sam.gov/opp/...",
           "sources":   ["description", "attachment:sow.pdf", ...],
           "clauses":   [ {"regulation":"FAR","number":"52.212-4","title":"..."}, ... ],
           "notes":     "...",
         },
         ...
      ]
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
ODD_FILL = PatternFill("solid", fgColor="F2F2F2")
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)


def _style_header(ws, ncols: int):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = ws["A2"]


def _autosize(ws, min_w: int = 8, max_w: int = 60):
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        best = 0
        for cell in col:
            v = cell.value
            if v is None:
                continue
            best = max(best, min(max_w, max(len(str(v).split("\n")[0]), min_w)))
        ws.column_dimensions[letter].width = best + 2


def write_scorecard(results: dict, out_path: Path) -> Path:
    contracts = results.get("contracts", []) or []

    # --- aggregate ---
    # clause_id -> {"regulation","number","citation","part","title", "contracts": set, "titles_seen": set}
    agg: dict[str, dict] = {}
    for c in contracts:
        cid = c.get("id") or c.get("title") or "?"
        for cl in c.get("clauses") or []:
            key = f"{cl['regulation']} {cl['number']}"
            slot = agg.setdefault(key, {
                "regulation": cl["regulation"],
                "number": cl["number"],
                "citation": key,
                "part": cl["number"].rsplit("-", 1)[0],
                "title": cl.get("title") or "",
                "contracts": set(),
                "titles_seen": set(),
            })
            slot["contracts"].add(cid)
            if cl.get("title"):
                slot["titles_seen"].add(cl["title"])

    total_contracts = len(contracts) or 1
    rows = []
    for key, s in agg.items():
        rows.append({
            "citation": s["citation"],
            "regulation": s["regulation"],
            "part": s["part"],
            "number": s["number"],
            "title": s["title"] or (next(iter(s["titles_seen"])) if s["titles_seen"] else ""),
            "count": len(s["contracts"]),
            "coverage": len(s["contracts"]) / total_contracts,
            "contract_ids": sorted(s["contracts"]),
        })
    rows.sort(key=lambda r: (-r["count"], r["regulation"], r["number"]))

    wb = Workbook()

    # === Sheet 1: Scorecard ============================================
    ws = wb.active
    ws.title = "Scorecard"
    ws.append(["Citation", "Regulation", "Part", "Number", "Title",
               "Count", "Coverage %", "Contract IDs"])
    for i, r in enumerate(rows, start=2):
        ws.append([
            r["citation"], r["regulation"], r["part"], r["number"],
            r["title"], r["count"], r["coverage"],
            ", ".join(r["contract_ids"]),
        ])
        # zebra
        if i % 2 == 0:
            for c in range(1, 9):
                ws.cell(row=i, column=c).fill = ODD_FILL
        # right-align count, format coverage as %
        ws.cell(row=i, column=6).alignment = CENTER
        cov = ws.cell(row=i, column=7)
        cov.number_format = "0.0%"
        cov.alignment = CENTER
        ws.cell(row=i, column=5).alignment = LEFT
        ws.cell(row=i, column=8).alignment = LEFT
    _style_header(ws, 8)
    _autosize(ws)
    ws.column_dimensions["E"].width = 55
    ws.column_dimensions["H"].width = 40

    # === Sheet 2: Contract x Clause matrix =============================
    ws2 = wb.create_sheet("Contract x Clause")
    contract_ids = [c.get("id") or c.get("title") or f"C{i+1}"
                    for i, c in enumerate(contracts)]
    header = ["Citation", "Title"] + contract_ids
    ws2.append(header)
    for i, r in enumerate(rows, start=2):
        row = [r["citation"], r["title"]]
        for cid in contract_ids:
            row.append("✓" if cid in r["contract_ids"] else "")
        ws2.append(row)
        if i % 2 == 0:
            for c in range(1, len(header) + 1):
                ws2.cell(row=i, column=c).fill = ODD_FILL
        # ticks centered
        for c in range(3, len(header) + 1):
            ws2.cell(row=i, column=c).alignment = CENTER
    _style_header(ws2, len(header))
    _autosize(ws2)
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 55
    for c_idx in range(3, len(header) + 1):
        ws2.column_dimensions[get_column_letter(c_idx)].width = 16
    ws2.freeze_panes = "C2"

    # === Sheet 3: Raw Contracts ========================================
    ws3 = wb.create_sheet("Raw Contracts")
    ws3.append([
        "Contract ID", "Title", "Agency", "NAICS", "PSC/FSC", "Set-Aside",
        "Posted", "Deadline", "Sources scanned", "# Clauses", "URL", "Notes",
    ])
    for i, c in enumerate(contracts, start=2):
        ws3.append([
            c.get("id"), c.get("title"), c.get("agency"), c.get("naics"),
            c.get("psc"), c.get("set_aside"), c.get("posted"), c.get("deadline"),
            ", ".join(c.get("sources") or []),
            len(c.get("clauses") or []),
            c.get("url"), c.get("notes"),
        ])
        if i % 2 == 0:
            for col in range(1, 13):
                ws3.cell(row=i, column=col).fill = ODD_FILL
        ws3.cell(row=i, column=2).alignment = LEFT
        ws3.cell(row=i, column=9).alignment = LEFT
    _style_header(ws3, 12)
    _autosize(ws3)
    ws3.column_dimensions["B"].width = 45
    ws3.column_dimensions["C"].width = 25
    ws3.column_dimensions["I"].width = 40
    ws3.column_dimensions["K"].width = 45

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
