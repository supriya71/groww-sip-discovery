"""Corpus hygiene: staff replies, manual corrections, Reddit flag."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CORRECTIONS_PATH = REPO_ROOT / "data" / "manual" / "corrections.csv"

_STAFF_REPLY = re.compile(
    r"(^|\n)\s*("
    r"thank you for (writing|reaching out|your feedback)|"
    r"we (regret|apologi[sz]e)|"
    r"this is (the )?(groww|paytm|et ?money|zerodha) team|"
    r"dear user,|hi there,?\s*sorry|"
    r"team groww|team paytm|paytm money support|"
    r"groww support|official response"
    r")",
    re.I,
)
_STAFF_META = re.compile(r"\b(developer reply|response from developer|posted by admin)\b", re.I)


def is_platform_staff_reply(row: dict[str, Any]) -> bool:
    if row.get("author_type") in ("staff", "developer", "official"):
        return True
    if row.get("is_developer_reply") or row.get("developer_reply"):
        return True
    text = (row.get("text") or "").strip()
    if not text:
        return False
    if _STAFF_META.search(text):
        return True
    if len(text) > 40 and _STAFF_REPLY.search(text[:500]):
        return True
    return False


def is_curated_reddit(row: dict[str, Any]) -> bool:
    return (row.get("source") or "") == "reddit"


def load_corrections() -> list[dict[str, str]]:
    if not CORRECTIONS_PATH.is_file():
        return []
    rows: list[dict[str, str]] = []
    with CORRECTIONS_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row.get("id") or row["id"].startswith("#"):
                continue
            rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


def apply_corrections(row: dict[str, Any], corrections: list[dict[str, str]]) -> dict[str, Any]:
    iid = row.get("id")
    if not iid:
        return row
    tags = dict(row.get("tags") or {})
    changed = False
    for corr in corrections:
        if corr.get("id") != iid:
            continue
        field = corr.get("field") or ""
        if field == "relevant":
            row["filter_relevant"] = corr.get("new_value") or row.get("filter_relevant")
            if corr.get("new_value") == "yes":
                tags["relevance_flag"] = "Y"
            elif corr.get("new_value") == "no":
                tags["relevance_flag"] = "N"
            changed = True
        elif field in tags:
            tags[field] = corr.get("new_value") or tags[field]
            changed = True
        elif field == "quote":
            row["quote"] = corr.get("new_value") or row.get("quote")
            changed = True
    if changed:
        row["tags"] = tags
        row["manual_correction_applied"] = True
    return row
