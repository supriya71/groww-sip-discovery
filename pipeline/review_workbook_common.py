"""Shared column labels and field mapping for human review workbook."""

from __future__ import annotations

import re
from typing import Any

from pipeline.export import (
    TAG_FIELDS,
    expand_code,
    is_relevant,
    severity_score,
    verified_quote,
    voluntary_bucket,
)
from pipeline.tag_audit import suspicious_reason

_BEGINNER = re.compile(
    r"\b(beginner|first time|first-time|new investor|pehli bar|pahli bar|started sip)\b",
    re.I,
)

SHEET_SUMMARY = "Summary"
SHEET_TAGGED = "All tagged posts"
SHEET_UNTAGGED = "Not yet tagged"
SHEET_DROPPED = "Dropped"

REVIEW_HEADERS = (
    "REVIEW: correct?",
    "REVIEW: field to fix",
    "REVIEW: correct value",
    "REVIEW: note",
)

TAGGED_HEADERS: tuple[str, ...] = (
    "id",
    "source",
    "year",
    "link",
    "post text",
    "context",
    "relevant",
    "journey stage",
    "SIP action",
    "lapse type",
    "stated triggers",
    "deeper reason",
    "unmet need",
    "emotion",
    "severity",
    "first-time investor",
    "segment signals",
    "evidence quote",
    "quote verified?",
    "tagged by",
    "needs review",
) + REVIEW_HEADERS

UNTAGGED_HEADERS: tuple[str, ...] = (
    "id",
    "source",
    "year",
    "link",
    "post text",
    "context",
    "filter relevant",
    "filter reason",
)

DROPPED_HEADERS: tuple[str, ...] = (
    "id",
    "source",
    "year",
    "link",
    "post text",
    "drop reason",
    "detail",
)

# Display label -> (internal field key, use_voluntary_bucket)
FIELD_FIX_OPTIONS: list[tuple[str, str]] = [
    ("Relevant", "relevant"),
    ("Journey stage", "journey_stage"),
    ("SIP action", "lapse_type"),
    ("Lapse type (voluntary / involuntary)", "lapse_bucket"),
    ("Stated triggers", "drop_trigger"),
    ("Deeper reason", "expectation"),
    ("Unmet need", "in_app_friction"),
    ("Emotion", "sentiment"),
    ("Segment signals", "investor_segment"),
    ("Evidence quote", "quote"),
]

FIELD_FIX_LABELS = [label for label, _ in FIELD_FIX_OPTIONS]
FIELD_FIX_TO_KEY = {label: key for label, key in FIELD_FIX_OPTIONS}

SIP_ACTION_LABELS = {
    "UNC": "Unclear",
    "NON": "Continuing SIP (no lapse)",
    "PAU": "Paused SIP",
    "RED": "Reduced SIP amount",
    "STO": "Stopped / cancelled SIP",
    "CON": "Considering pause or stop",
    "NA": "Not applicable",
}

LAPSE_BUCKET_LABELS = {
    "voluntary": "Voluntary lapse",
    "involuntary": "Involuntary (mandate / system)",
    "unclear": "Unclear",
    "continuing": "No lapse (continuing)",
}


def _tag(row: dict[str, Any], field: str) -> str:
    return str((row.get("tags") or {}).get(field) or "")


def row_link(row: dict[str, Any]) -> str:
    url = row.get("url") or row.get("permalink") or row.get("link")
    if url:
        return str(url)
    src = row.get("source") or ""
    if src == "play_store" and row.get("app_id"):
        return f"https://play.google.com/store/apps/details?id={row['app_id']}"
    if src == "youtube" and row.get("video_id"):
        return f"https://www.youtube.com/watch?v={row['video_id']}"
    return ""


def first_time_label(row: dict[str, Any]) -> str:
    text = (row.get("text") or "").lower()
    stage = _tag(row, "journey_stage")
    if stage in {"S0", "S1"}:
        return "Likely yes"
    if _BEGINNER.search(text):
        return "Yes (text signal)"
    if stage in {"S4", "S5"}:
        return "Likely no"
    return "Unclear"


def needs_review_flag(row: dict[str, Any]) -> str:
    reasons: list[str] = []
    if (row.get("filter_relevant") or "") == "unsure":
        reasons.append("filter_unsure")
    susp = suspicious_reason(row)
    if susp:
        reasons.append(susp)
    unc_fields = [f for f in TAG_FIELDS if _tag(row, f) == "UNC"]
    if len(unc_fields) >= 3:
        reasons.append("many_unclear_tags")
    if not verified_quote(row) and (row.get("quote") or "").strip():
        reasons.append("quote_not_verified")
    return "; ".join(reasons) if reasons else ""


def relevant_display(row: dict[str, Any]) -> str:
    fr = (row.get("filter_relevant") or "").lower()
    if is_relevant(row):
        return "yes"
    if fr == "no":
        return "no"
    return fr or "unsure"


def tagged_row_values(row: dict[str, Any], codebook: dict[str, Any]) -> list[Any]:
    def exp(field: str) -> str:
        return expand_code(codebook, field, _tag(row, field))

    bucket = voluntary_bucket(row)
    if _tag(row, "lapse_type") == "NON":
        bucket_label = LAPSE_BUCKET_LABELS["continuing"]
    else:
        bucket_label = LAPSE_BUCKET_LABELS.get(bucket, bucket)

    lt_code = _tag(row, "lapse_type")
    sip_action = SIP_ACTION_LABELS.get(lt_code, exp("lapse_type"))

    return [
        row.get("id"),
        row.get("source"),
        row.get("year"),
        row_link(row),
        row.get("text") or "",
        row.get("context") or "",
        relevant_display(row),
        exp("journey_stage"),
        sip_action,
        bucket_label,
        exp("drop_trigger"),
        exp("expectation"),
        exp("in_app_friction"),
        exp("sentiment"),
        severity_score(row),
        first_time_label(row),
        exp("investor_segment"),
        row.get("quote") or "",
        "Y" if verified_quote(row) else "N",
        row.get("tagged_by") or row.get("model") or "",
        needs_review_flag(row),
        "",
        "",
        "",
        "",
    ]


def codebook_dropdown_values(codebook: dict[str, Any]) -> list[str]:
    """All allowed codes and 'CODE — label' strings for Excel validation."""
    values: list[str] = []
    seen: set[str] = set()
    for field in TAG_FIELDS:
        codes = (codebook.get("codes") or {}).get(field) or {}
        for code, label in codes.items():
            for val in (str(code), f"{code} — {label}"):
                if val not in seen:
                    seen.add(val)
                    values.append(val)
    for val in ("yes", "no", "unsure", "Y", "N", "U"):
        if val not in seen:
            seen.add(val)
            values.append(val)
    return sorted(values, key=str.lower)


def parse_corrected_value(raw: str, codebook: dict[str, Any]) -> str:
    """Normalize Excel cell to stored correction value (usually short code)."""
    text = (raw or "").strip()
    if not text:
        return ""
    if " — " in text:
        text = text.split(" — ", 1)[0].strip()
    lower = text.lower()
    if lower in {"yes", "no", "unsure"}:
        return lower
    for field in TAG_FIELDS:
        codes = (codebook.get("codes") or {}).get(field) or {}
        if text in codes:
            return text
        for code, label in codes.items():
            if text.lower() == str(label).lower():
                return str(code)
    return text


def old_value_for_field(row: dict[str, Any], field_key: str, codebook: dict[str, Any]) -> str:
    if field_key == "relevant":
        return relevant_display(row)
    if field_key == "lapse_bucket":
        bucket = voluntary_bucket(row)
        if _tag(row, "lapse_type") == "NON":
            return LAPSE_BUCKET_LABELS["continuing"]
        return LAPSE_BUCKET_LABELS.get(bucket, bucket)
    if field_key == "quote":
        return (row.get("quote") or "").strip()
    code = _tag(row, field_key)
    if code:
        return code
    return ""


def map_field_fix_to_correction(
    field_label: str, new_raw: str, row: dict[str, Any], codebook: dict[str, Any]
) -> tuple[str, str, str] | None:
    """Return (id, field, new_value) for corrections.csv or None."""
    key = FIELD_FIX_TO_KEY.get(field_label.strip())
    if not key:
        return None
    new_val = parse_corrected_value(new_raw, codebook)
    if not new_val:
        return None
    iid = row.get("id")
    if not iid:
        return None

    if key == "lapse_bucket":
        return None

    if key == "relevant":
        if new_val not in {"yes", "no", "unsure"}:
            return None
        return (iid, "relevant", new_val)

    if key == "quote":
        return (iid, "quote", new_val)

    if key not in TAG_FIELDS:
        return None
    return (iid, key, new_val)
