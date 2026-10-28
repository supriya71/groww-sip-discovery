"""Sample suspicious tags and log audit for export / evidence."""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_LOG = Path(__file__).resolve().parents[1] / "docs" / "evidence_log_notes.md"

_TRADING = re.compile(r"\b(stop-loss|stop loss|intraday|nifty|banknifty|share|trading)\b", re.I)
_NEG = re.compile(r"\b(worst|bad|bakwas|fraud|useless|hate|terrible|pathetic)\b", re.I)
_POS = re.compile(r"\b(good|best|love|smooth|excellent)\b", re.I)


def _tag(row: dict[str, Any], field: str) -> str:
    return str((row.get("tags") or {}).get(field) or "")


def suspicious_reason(row: dict[str, Any]) -> str | None:
    text = (row.get("text") or "").lower()
    if _tag(row, "drop_trigger") == "UR" and _TRADING.search(text) and "mutual" not in text and "sip" not in text:
        return "UR_with_trading_not_MF"
    if _tag(row, "lapse_type") == "NON" and re.search(r"\b(stop|cancel|pause)\b", text):
        return "NON_with_stop_language"
    if _tag(row, "sentiment") == "POS" and _NEG.search(text):
        return "POS_with_negative_words"
    if _tag(row, "sentiment") == "NEG" and text.strip().startswith("good"):
        return "NEG_on_positive_opening"
    if row.get("filter_relevant") == "yes" and "sip" not in text and "mutual" not in text:
        if not re.search(r"\b(mf|nav|elss|mandate|autopay)\b", text):
            return "relevant_yes_weak_MF_signal"
    return None


def run_tag_audit(rows: list[dict[str, Any]], *, seed: int = 11, sample_n: int = 20) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        reason = suspicious_reason(row)
        if reason:
            buckets.setdefault(reason, []).append(row)

    audits: list[dict[str, Any]] = []
    rng = random.Random(seed)
    for reason, items in sorted(buckets.items()):
        pick = items if len(items) <= sample_n else rng.sample(items, sample_n)
        audits.append(
            {
                "reason": reason,
                "population": len(items),
                "sample_size": len(pick),
                "samples": [
                    {
                        "id": r.get("id"),
                        "source": r.get("source"),
                        "quote": (r.get("quote") or r.get("text") or "")[:160],
                        "tags": r.get("tags"),
                    }
                    for r in pick
                ],
            }
        )

    report = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "rule_fix": "pipeline/rules_engine.py v2 (UR MF-only, lapse/stop, sentiment order)",
        "audits": audits,
    }
    append_audit_log(report)
    return report


def append_audit_log(report: dict[str, Any]) -> None:
    lines = [
        "",
        f"## Tag audit ({report['audited_at'][:10]})",
        "",
        f"- Fix applied: {report.get('rule_fix')}",
        "",
    ]
    for block in report.get("audits") or []:
        lines.append(f"### {block['reason']} (n={block['population']}, sampled {block['sample_size']})")
        for s in block.get("samples", [])[:5]:
            lines.append(f"- `{s.get('id')}`: {(s.get('quote') or '')[:100]}")
        lines.append("")
    existing = EVIDENCE_LOG.read_text(encoding="utf-8") if EVIDENCE_LOG.is_file() else "# Evidence log\n"
    EVIDENCE_LOG.write_text(existing + "\n".join(lines), encoding="utf-8")
