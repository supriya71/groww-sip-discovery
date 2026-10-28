"""Select 20 relevant posts and export human labelling sheet (no AI tags shown).

Run from repo root: python -m pipeline.labelling_export
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REVIEW_DIR = REPO_ROOT / "data" / "review"
WORKBOOK_PATH = REVIEW_DIR / "labelling_sheet.xlsx"
MANIFEST_PATH = REPO_ROOT / "data" / "manual" / "labelling_sample.json"

from pipeline.export import is_relevant, load_codebook, load_tag_rows, merge_records  # noqa: E402
from pipeline.filter import item_id, load_clean_rows  # noqa: E402
from pipeline.labelling_sheet_common import (  # noqa: E402
    LABELLING_HEADERS,
    SHEET_CODES,
    SHEET_LABELLING,
    dropdown_lists,
    enrich_from_clean,
)
from pipeline.tag_audit import suspicious_reason  # noqa: E402

SAMPLE_N = 20
SEED = 42

HEADER_FILL = PatternFill("solid", fgColor="D9EAD3")
HUMAN_FILL = PatternFill("solid", fgColor="E8F4FC")
WRAP = Alignment(wrap_text=True, vertical="top")
HEADER_FONT = Font(bold=True)

# Five connector sources in brief; use app platform strata when corpus is play_store-only.


def _tags(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("tags") or {}


def load_relevant_pool() -> list[dict[str, Any]]:
    clean = {item_id(r): r for r in load_clean_rows()}
    return [
        enrich_from_clean(r, clean)
        for r in merge_records(load_tag_rows(), clean)
        if is_relevant(r)
    ]


def select_sample(pool: list[dict[str, Any]], *, seed: int = SEED) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    used: set[str] = set()
    picked: list[dict[str, Any]] = []

    def candidates(filter_fn) -> list[dict[str, Any]]:
        return [r for r in pool if filter_fn(r) and r["id"] not in used]

    strata: list[tuple[str, str, Any]] = [
        ("platform:Groww", "App platform (Groww)", lambda r: r.get("app_label") == "Groww"),
        ("platform:Paytm", "App platform (Paytm Money)", lambda r: r.get("app_label") == "Paytm Money"),
        ("platform:Coin", "App platform (Coin by Zerodha)", lambda r: r.get("app_label") == "Coin by Zerodha"),
        (
            "language:hinglish",
            "Hinglish / Hindi",
            lambda r: (r.get("language") or "") in ("hinglish", "hi"),
        ),
        ("hard:audit", "Hard case (tag audit flag)", lambda r: bool(suspicious_reason(r))),
        ("lapse:STO", "Stopped / cancelled SIP", lambda r: _tags(r).get("lapse_type") == "STO"),
        ("lapse:PAU", "Paused SIP", lambda r: _tags(r).get("lapse_type") == "PAU"),
        ("trigger:UR", "Underwhelming returns trigger", lambda r: _tags(r).get("drop_trigger") == "UR"),
        ("trigger:MD", "Mandate failure trigger", lambda r: _tags(r).get("drop_trigger") == "MD"),
        ("trigger:CF", "Cash-flow trigger", lambda r: _tags(r).get("drop_trigger") == "CF"),
        ("trigger:MF", "Market fear trigger", lambda r: _tags(r).get("drop_trigger") == "MF"),
        ("lapse:NON-edge", "Continuing but negative tone", lambda r: _tags(r).get("lapse_type") == "NON"
         and (r.get("text") or "").lower().find("stop") >= 0),
    ]

    progressed = True
    while len(picked) < SAMPLE_N and progressed:
        progressed = False
        for stratum, reason, filter_fn in strata:
            if len(picked) >= SAMPLE_N:
                break
            opts = candidates(filter_fn)
            if not opts:
                continue
            rng.shuffle(opts)
            row = opts[0]
            used.add(row["id"])
            picked.append({**row, "_stratum": stratum, "_selection_reason": reason})
            progressed = True

    rest = [r for r in pool if r["id"] not in used]
    rng.shuffle(rest)
    for row in rest:
        if len(picked) >= SAMPLE_N:
            break
        used.add(row["id"])
        picked.append({**row, "_stratum": "fill", "_selection_reason": "Diverse fill"})

    return picked[:SAMPLE_N]


def write_code_lists(ws, lists: dict[str, list[str]]) -> None:
    ws.cell(row=1, column=1, value="field")
    ws.cell(row=1, column=2, value="value")
    row = 2
    ranges: dict[str, str] = {}
    for field, values in lists.items():
        start = row
        for val in values:
            ws.cell(row=row, column=1, value=field)
            ws.cell(row=row, column=2, value=val)
            row += 1
        end = row - 1
        if values:
            ranges[field] = f"'{SHEET_CODES}'!$B${start}:$B${end}"
    return ranges


def add_validations(ws, ranges: dict[str, str], nrows: int) -> None:
    last = max(nrows + 1, 50)
    col_map = {h: i + 1 for i, h in enumerate(LABELLING_HEADERS)}
    for field in ("is_relevant", "sip_action", "lapse_type", "emotion", "investor_type"):
        ref = ranges.get(field)
        if not ref:
            continue
        col = get_column_letter(col_map[field])
        dv = DataValidation(type="list", formula1=f"={ref}", allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{last}")
    # Multi triggers: free text + reference list on Code lists sheet (no strict validation)


def run_export(*, seed: int = SEED) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    codebook = load_codebook()
    pool = load_relevant_pool()
    sample = select_sample(pool, seed=seed)

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_LABELLING
    ws.cell(
        row=1,
        column=1,
        value=(
            "Human labels only (no AI tags). stated_triggers: comma/semi-colon separate codes — see Code lists. "
            "Then: python -m pipeline.labelling_score && python -m pipeline.export"
        ),
    )
    ws.merge_cells(
        start_row=1,
        start_column=1,
        end_row=1,
        end_column=len(LABELLING_HEADERS),
    )

    for col, title in enumerate(LABELLING_HEADERS, start=1):
        cell = ws.cell(row=2, column=col, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL if title in ("post text", "context", "id") else HUMAN_FILL

    for i, row in enumerate(sample, start=3):
        ws.cell(row=i, column=1, value=row["id"])
        ws.cell(row=i, column=2, value=row.get("text") or "")
        ws.cell(row=i, column=3, value=row.get("context") or "")
        for col in range(4, len(LABELLING_HEADERS) + 1):
            ws.cell(row=i, column=col, value="")

    ws.column_dimensions["A"].hidden = True
    ws.column_dimensions["B"].width = 58
    ws.column_dimensions["C"].width = 28
    for col in range(4, len(LABELLING_HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 22
    for r in range(3, 3 + len(sample)):
        ws.cell(row=r, column=2).alignment = WRAP
        ws.cell(row=r, column=3).alignment = WRAP
    ws.freeze_panes = "B3"

    ws_codes = wb.create_sheet(SHEET_CODES)
    lists = dropdown_lists(codebook)
    ranges = write_code_lists(ws_codes, lists)

    add_validations(ws, ranges, len(sample))

    wb.save(WORKBOOK_PATH)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workbook": str(WORKBOOK_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "sample_size": len(sample),
        "seed": seed,
        "strata_note": (
            "Tagged corpus is play_store-only today; spread uses app platform (Groww / Paytm Money / "
            "Coin), Hinglish, hard-case audit flags, and diverse lapse/trigger codes. "
            "When reddit, youtube, tradingqna, and app_store are tagged, re-run export for true 5-source spread."
        ),
        "items": [
            {
                "id": r["id"],
                "stratum": r.get("_stratum"),
                "selection_reason": r.get("_selection_reason"),
                "app_label": r.get("app_label"),
                "language": r.get("language"),
            }
            for r in sample
        ],
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return WORKBOOK_PATH


def main() -> int:
    path = run_export()
    print(f"Wrote {path.relative_to(REPO_ROOT)}")
    print(f"  Manifest: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    print(f"  Sample size: {manifest['sample_size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
