"""Human labelling sheet — fields, dropdowns, AI vs human comparison."""

from __future__ import annotations

import re
from typing import Any

from pipeline.export import expand_code, is_relevant, voluntary_bucket
from pipeline.review_workbook_common import (
    LAPSE_BUCKET_LABELS,
    SIP_ACTION_LABELS,
    parse_corrected_value,
)

EVIDENCE_LOG = __import__("pathlib").Path(__file__).resolve().parents[1] / "docs" / "evidence_log_notes.md"

SHEET_LABELLING = "Labelling"
SHEET_CODES = "Code lists"

# Hidden id + visible text + human labels (no AI columns on sheet)
LABELLING_HEADERS: tuple[str, ...] = (
    "id",
    "post text",
    "context",
    "is_relevant",
    "sip_action",
    "lapse_type",
    "stated_triggers",
    "emotion",
    "investor_type",
)

HUMAN_FIELDS = (
    "is_relevant",
    "sip_action",
    "lapse_type",
    "stated_triggers",
    "emotion",
    "investor_type",
)

FIELD_LABELS = {
    "is_relevant": "Is relevant",
    "sip_action": "SIP action",
    "lapse_type": "Lapse type (voluntary / involuntary)",
    "stated_triggers": "Stated triggers (multi)",
    "emotion": "Emotion",
    "investor_type": "Investor type",
}

RELEVANT_OPTIONS = ("yes", "no", "unsure")

LABEL_TO_LAPSE_CODE = {v: k for k, v in SIP_ACTION_LABELS.items()}
LABEL_TO_LAPSE_CODE.update({k: k for k in SIP_ACTION_LABELS})

BUCKET_LABEL_TO_KEY = {v: k for k, v in LAPSE_BUCKET_LABELS.items()}
if "No lapse (continuing)" in LAPSE_BUCKET_LABELS.values():
    pass
# continuing label maps to continuing key
BUCKET_LABEL_TO_KEY[LAPSE_BUCKET_LABELS["continuing"]] = "continuing"

_SPLIT_TRIGGERS = re.compile(r"[,;|]+")


def _tag(row: dict[str, Any], field: str) -> str:
    return str((row.get("tags") or {}).get(field) or "")


def enrich_from_clean(row: dict[str, Any], clean: dict[str, dict[str, Any]]) -> dict[str, Any]:
    base = clean.get(row.get("id") or "", {})
    return {
        **row,
        "text": base.get("text") or row.get("text") or "",
        "context": base.get("context") or row.get("context"),
        "language": base.get("language") or row.get("language"),
        "app_label": base.get("app_label"),
        "app_id": base.get("app_id"),
    }


def ai_values(row: dict[str, Any], codebook: dict[str, Any]) -> dict[str, Any]:
    lt = _tag(row, "lapse_type")
    bucket = voluntary_bucket(row)
    if lt == "NON":
        bucket_key = "continuing"
    else:
        bucket_key = bucket
    rel = "yes" if is_relevant(row) else (
        "no" if (row.get("filter_relevant") or "").lower() == "no" else "unsure"
    )
    trig = _tag(row, "drop_trigger")
    triggers = {trig} if trig and trig not in {"", "NA"} else set()
    return {
        "is_relevant": rel,
        "sip_action": lt,
        "lapse_type": bucket_key,
        "stated_triggers": triggers,
        "emotion": _tag(row, "sentiment"),
        "investor_type": _tag(row, "investor_segment"),
    }


def human_values(cells: dict[str, str], codebook: dict[str, Any]) -> dict[str, Any]:
    rel = (cells.get("is_relevant") or "").strip().lower()
    if rel not in RELEVANT_OPTIONS:
        rel = ""

    sip_raw = (cells.get("sip_action") or "").strip()
    sip_code = parse_corrected_value(sip_raw, codebook)
    if sip_code in SIP_ACTION_LABELS:
        pass
    elif sip_raw in LABEL_TO_LAPSE_CODE:
        sip_code = LABEL_TO_LAPSE_CODE[sip_raw]
    elif sip_raw:
        sip_code = sip_raw

    lapse_raw = (cells.get("lapse_type") or "").strip()
    lapse_key = BUCKET_LABEL_TO_KEY.get(lapse_raw, lapse_raw.lower())

    trig_raw = cells.get("stated_triggers") or ""
    triggers: set[str] = set()
    for part in _SPLIT_TRIGGERS.split(trig_raw):
        p = part.strip()
        if not p:
            continue
        code = parse_corrected_value(p, codebook)
        if code:
            triggers.add(code)

    emo = parse_corrected_value((cells.get("emotion") or "").strip(), codebook)
    inv = parse_corrected_value((cells.get("investor_type") or "").strip(), codebook)

    return {
        "is_relevant": rel,
        "sip_action": sip_code or "",
        "lapse_type": lapse_key or "",
        "stated_triggers": triggers,
        "emotion": emo or "",
        "investor_type": inv or "",
    }


def display_ai(codebook: dict[str, Any], field: str, value: Any) -> str:
    if field == "is_relevant":
        return str(value)
    if field == "sip_action":
        return SIP_ACTION_LABELS.get(str(value), expand_code(codebook, "lapse_type", str(value)))
    if field == "lapse_type":
        return LAPSE_BUCKET_LABELS.get(str(value), str(value))
    if field == "stated_triggers":
        if isinstance(value, set):
            return ", ".join(
                expand_code(codebook, "drop_trigger", c) for c in sorted(value) if c
            )
        return str(value)
    if field == "emotion":
        return expand_code(codebook, "sentiment", str(value))
    if field == "investor_type":
        return expand_code(codebook, "investor_segment", str(value))
    return str(value)


def display_human(codebook: dict[str, Any], field: str, value: Any) -> str:
    return display_ai(codebook, field, value)


def fields_agree(field: str, human: Any, ai: Any) -> bool:
    if field == "stated_triggers":
        h = human if isinstance(human, set) else set()
        a = ai if isinstance(ai, set) else set()
        return h == a
    return str(human or "").strip().upper() == str(ai or "").strip().upper()


def dropdown_lists(codebook: dict[str, Any]) -> dict[str, list[str]]:
    codes = codebook.get("codes") or {}
    lapse_labels = [SIP_ACTION_LABELS[k] for k in sorted(SIP_ACTION_LABELS)]
    bucket_labels = list(LAPSE_BUCKET_LABELS.values())
    trig = [
        f"{c} — {codes.get('drop_trigger', {}).get(c, c)}"
        for c in sorted((codes.get("drop_trigger") or {}).keys())
    ]
    sentiment = [
        f"{c} — {codes.get('sentiment', {}).get(c, c)}"
        for c in sorted((codes.get("sentiment") or {}).keys())
    ]
    segment = [
        f"{c} — {codes.get('investor_segment', {}).get(c, c)}"
        for c in sorted((codes.get("investor_segment") or {}).keys())
    ]
    return {
        "is_relevant": list(RELEVANT_OPTIONS),
        "sip_action": lapse_labels,
        "lapse_type": bucket_labels,
        "stated_triggers": trig,
        "emotion": sentiment,
        "investor_type": segment,
    }


def append_labelling_evidence_log(report: dict[str, Any]) -> None:
    lines = [
        "",
        f"## Human labelling agreement ({report.get('scored_at', '')[:10]})",
        "",
        f"- Sample size: **{report.get('sample_size', 0)}**",
        f"- Overall field agreement: **{report.get('overall_pct')}%**",
        f"- Strata note: {report.get('strata_note', '')}",
        "",
        "### Field agreement",
        "",
    ]
    for field, stats in (report.get("fields") or {}).items():
        lines.append(
            f"- **{FIELD_LABELS.get(field, field)}**: {stats.get('agree')}/{stats.get('total')} "
            f"({stats.get('pct')}%)"
        )
    lines.append("")
    lines.append("### Disagreements")
    lines.append("")
    for d in report.get("disagreements") or []:
        lines.append(
            f"- `{d.get('id')}` **{d.get('field')}**: human={d.get('human')!r} vs AI={d.get('ai')!r} "
            f"— {(d.get('snippet') or '')[:80]}"
        )
    lines.append("")
    existing = EVIDENCE_LOG.read_text(encoding="utf-8") if EVIDENCE_LOG.is_file() else "# Evidence log\n"
    EVIDENCE_LOG.write_text(existing + "\n".join(lines), encoding="utf-8")
