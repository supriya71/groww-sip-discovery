"""Build read-only dashboard bundle under data/export/ for Streamlit.

Run from repo root: python -m pipeline.export
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROCESSED = REPO_ROOT / "data" / "processed"
EXPORT = REPO_ROOT / "data" / "export"
CODEBOOK_PATH = REPO_ROOT / "codebook.yaml"
EVIDENCE_LOG = REPO_ROOT / "docs" / "evidence_log_notes.md"

from pipeline.corpus_filters import (
    apply_corrections,
    is_curated_reddit,
    is_platform_staff_reply,
    load_corrections,
)
from pipeline.rules_engine import tag_item
from pipeline.tag_audit import run_tag_audit

TAG_FIELDS = (
    "journey_stage",
    "lapse_type",
    "drop_trigger",
    "expectation",
    "in_app_friction",
    "investor_segment",
    "sentiment",
    "relevance_flag",
)

USABILITY_SUBTHEMES: list[tuple[str, str, re.Pattern[str]]] = [
    ("app_stability", "Crashes / hangs / won't open", re.compile(r"\b(crash|hang|not open|freeze|force close)\b", re.I)),
    ("slow_performance", "Slow or laggy", re.compile(r"\b(slow|lag|loading|buffer)\b", re.I)),
    ("navigation_ui", "Confusing navigation or UI", re.compile(r"\b(confus|hard to use|difficult|ui|navigate)\b", re.I)),
    ("auth_login", "Login / OTP / verification", re.compile(r"\b(login|otp|verify|kyc)\b", re.I)),
    ("sip_flows", "Pause / modify / SIP flow", re.compile(r"\b(pause|modify|cancel sip|mandate|autopay)\b", re.I)),
    ("support_access", "Support hard to reach", re.compile(r"\b(customer care|support|ticket|call)\b", re.I)),
]
VOLuntary_LAPSE = {"PAU", "RED", "STO", "CON"}
INVOLUNTARY_TRIGGERS = {"MD"}

CAUSE_LABELS = {
    "CF": "Cash flow / EMI pressure",
    "MF": "Market fear / volatility",
    "UR": "Underwhelming returns",
    "CX": "Complexity / confusion",
    "LE": "Life event",
    "GA": "Goal achieved",
    "SP": "Switched platform",
    "SU": "Bad support",
    "MD": "Mandate / autopay failure",
    "UNC": "Unclear cause",
}

METRIC_BRANCHES = [
    ("journey_stage", "Journey stage"),
    ("lapse_type", "Lapse behaviour"),
    ("drop_trigger", "Drop trigger"),
    ("expectation", "Expectation type"),
    ("in_app_friction", "In-app friction"),
    ("investor_segment", "Investor segment (proxy)"),
]


def _tag(row: dict[str, Any], field: str) -> str:
    tags = row.get("tags") or {}
    return str(tags.get(field) or "")


def _has_any(row: dict[str, Any], pattern: str) -> bool:
    text = (row.get("text") or "").lower()
    return bool(re.search(pattern, text, re.I))


DISCOVERY_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "Q1",
        "title": "Why start a SIP, and what do they expect?",
        "match": lambda r: _tag(r, "journey_stage") in {"S0", "S1"}
        or _tag(r, "expectation") not in {"UNC", "NA", ""},
    },
    {
        "id": "Q2",
        "title": "What changes in behaviour after starting?",
        "match": lambda r: _tag(r, "journey_stage") in {"S2", "S3"}
        or _has_any(r, r"\b(month|week|check|open app|statement)\b"),
    },
    {
        "id": "Q3",
        "title": "What leads to pause, reduce, or stop?",
        "match": lambda r: _tag(r, "lapse_type") in VOLuntary_LAPSE
        or _tag(r, "drop_trigger") not in {"UNC", "NA", ""},
    },
    {
        "id": "Q4",
        "title": "What do investors check, and how often?",
        "match": lambda r: _tag(r, "in_app_friction") in {"ST", "PF", "NAV"}
        or _has_any(r, r"\b(nav|xirr|return|statement|portfolio|check)\b"),
    },
    {
        "id": "Q5",
        "title": "In-app confusion or worry",
        "match": lambda r: _tag(r, "in_app_friction") not in {"UNC", "NA", ""},
    },
    {
        "id": "Q6",
        "title": "Out-of-app coping when unsure",
        "match": lambda r: _has_any(
            r,
            r"\b(youtube|reddit|friend|ca\b|chartered|twitter|google|quora)\b",
        )
        or _tag(r, "investor_segment") == "INF",
    },
    {
        "id": "Q7",
        "title": "Continuers vs quiet drop-offs",
        "match": lambda r: _tag(r, "lapse_type") in {"NON", "STO", "PAU", "RED"},
    },
    {
        "id": "Q8",
        "title": "Patterns by segment (when stated)",
        "match": lambda r: _tag(r, "investor_segment") not in {"UNC", "NA", ""},
    },
    {
        "id": "Q9",
        "title": "Repeated unmet needs",
        "match": lambda r: _has_any(
            r,
            r"\b(i wish|there should|should be|need a feature|unmet|workaround)\b",
        )
        or _tag(r, "in_app_friction") in {"UI", "PM", "NT"},
    },
]


def is_relevant(row: dict[str, Any]) -> bool:
    if row.get("filter_relevant") == "yes":
        return True
    return _tag(row, "relevance_flag") == "Y"


def load_codebook() -> dict[str, Any]:
    with CODEBOOK_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def expand_code(codebook: dict[str, Any], field: str, code: str) -> str:
    codes = (codebook.get("codes") or {}).get(field) or {}
    return str(codes.get(code) or code)


def load_clean_index() -> dict[str, dict[str, Any]]:
    path = PROCESSED / "clean.jsonl"
    index: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return index
    from pipeline.filter import item_id

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        index[item_id(row)] = row
    return index


def load_tag_rows() -> list[dict[str, Any]]:
    path = PROCESSED / "tag_results.jsonl"
    if not path.is_file():
        return []
    by_id: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("tag_status") != "ok":
            continue
        iid = rec.get("id")
        if not iid:
            continue
        prev = by_id.get(iid)
        if prev is None:
            by_id[iid] = rec
            continue
        if rec.get("pilot_mode") == "rules_fallback" and prev.get("pilot_mode") != "rules_fallback":
            continue
        if (rec.get("tagged_by") or "").endswith("120b"):
            by_id[iid] = rec
    return list(by_id.values())


def reapply_rules_tags(row: dict[str, Any]) -> dict[str, Any]:
    orig_rel = row.get("filter_relevant") or "unsure"
    tagged = tag_item(row, {"relevant": orig_rel, "reason": "export_retag"})
    row["tags"] = {f: tagged[f] for f in TAG_FIELDS}
    row["quote"] = tagged.get("quote") or row.get("quote")
    row["filter_relevant"] = orig_rel
    row["relevant"] = is_relevant(row)
    return row


def prepare_export_rows(
    tags: list[dict[str, Any]], clean: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    corrections = load_corrections()
    rows: list[dict[str, Any]] = []
    staff_skipped = 0
    for rec in tags:
        iid = rec["id"]
        base = clean.get(iid, {})
        merged = {
            **rec,
            "text": base.get("text") or rec.get("quote") or "",
            "context": base.get("context"),
            "language": base.get("language"),
        }
        if is_platform_staff_reply(merged):
            staff_skipped += 1
            continue
        merged = reapply_rules_tags(merged)
        merged = apply_corrections(merged, corrections)
        merged["relevant"] = is_relevant(merged)
        merged["is_curated_reddit"] = is_curated_reddit(merged)
        rows.append(merged)
    return rows, staff_skipped


def count_by_source(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        src = r.get("source") or "unknown"
        if src not in out:
            out[src] = {"tagged": 0, "relevant": 0}
        out[src]["tagged"] += 1
        if r.get("relevant"):
            out[src]["relevant"] += 1
    return out


def filter_rows(rows: list[dict[str, Any]], *, exclude_reddit: bool) -> list[dict[str, Any]]:
    if not exclude_reddit:
        return rows
    return [r for r in rows if not r.get("is_curated_reddit")]


def source_mix_line(by_source: dict[str, dict[str, int]], *, relevant: bool) -> str:
    key = "relevant" if relevant else "tagged"
    parts = [f"{src} {vals[key]:,}" for src, vals in sorted(by_source.items()) if vals[key]]
    return " · ".join(parts) if parts else "no data"


def build_usability_subthemes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rel = [r for r in rows if r.get("relevant")]
    rel_n = max(1, len(rel))
    items: list[dict[str, Any]] = []
    for code, label, pattern in USABILITY_SUBTHEMES:
        matched = [r for r in rel if pattern.search(r.get("text") or "")]
        if not matched:
            continue
        by_src: Counter[str] = Counter(m.get("source") or "unknown" for m in matched)
        sample = sorted(matched, key=severity_score, reverse=True)
        items.append(
            {
                "code": code,
                "label": label,
                "posts": len(matched),
                "pct_of_relevant": round(100.0 * len(matched) / rel_n, 2),
                "by_source": dict(by_src),
                "quotes": [r.get("quote", "") for r in sample[:2] if r.get("quote")],
            }
        )
    items.sort(key=lambda x: x["posts"], reverse=True)
    return items


def merge_records(tags: list[dict[str, Any]], clean: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows, _ = prepare_export_rows(tags, clean)
    return rows


def severity_score(row: dict[str, Any]) -> int:
    score = 1
    lapse = _tag(row, "lapse_type")
    if lapse in {"STO", "PAU"}:
        score += 2
    elif lapse in {"RED", "CON"}:
        score += 1
    trig = _tag(row, "drop_trigger")
    if trig in INVOLUNTARY_TRIGGERS:
        score += 1
    if _tag(row, "sentiment") == "NEG":
        score += 1
    if row.get("relevant"):
        score += 1
    return min(5, score)


def voluntary_bucket(row: dict[str, Any]) -> str:
    if _tag(row, "drop_trigger") in INVOLUNTARY_TRIGGERS:
        return "involuntary"
    if _tag(row, "lapse_type") in VOLuntary_LAPSE:
        return "voluntary"
    return "unclear"


def verified_quote(row: dict[str, Any]) -> bool:
    if row.get("quote_hidden"):
        return False
    score = row.get("quote_match_score") or 0
    return score >= 80 and bool(row.get("quote"))


def funnel_from_evidence() -> list[dict[str, Any]]:
    funnel = [
        {"stage": "Raw collected", "play_store": 68142, "total": 68142},
        {"stage": "After dedupe", "play_store": 38240, "total": 38240},
        {"stage": "After length filter (clean)", "play_store": 25587, "total": 25587},
    ]
    return funnel


def build_opportunities(rows: list[dict[str, Any]], codebook: dict[str, Any]) -> dict[str, Any]:
    rel = [r for r in rows if r.get("relevant")]
    rel_n = max(1, len(rel))

    def pack_causes() -> list[dict[str, Any]]:
        counts: Counter[str] = Counter()
        by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rel:
            code = _tag(r, "drop_trigger")
            if code in {"NA", ""}:
                continue
            counts[code] += 1
            by_code[code].append(r)
        items = []
        for code, n in counts.most_common():
            sample = sorted(by_code[code], key=severity_score, reverse=True)
            quotes = [r.get("quote", "") for r in sample[:2] if r.get("quote")]
            pct = round(100.0 * n / rel_n, 2)
            sev = round(sum(severity_score(r) for r in by_code[code]) / n, 2)
            fixable = code not in {"MF", "LE"}  # guardrails: can't fix macro/life
            score = round((n ** 0.5) * sev * (1.2 if fixable else 0.8), 2)
            by_src: Counter[str] = Counter(r.get("source") or "unknown" for r in by_code[code])
            items.append(
                {
                    "label": CAUSE_LABELS.get(code, code),
                    "code": code,
                    "posts": n,
                    "pct_of_relevant": pct,
                    "severity_avg": sev,
                    "metric_tree_branch": "drop_trigger",
                    "fixable_under_guardrails": fixable,
                    "opportunity_score": score,
                    "quotes": quotes,
                    "by_source": dict(by_src),
                }
            )
        items.sort(key=lambda x: x["opportunity_score"], reverse=True)
        return items

    def pack_after_stop() -> list[dict[str, Any]]:
        counts: Counter[str] = Counter()
        by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rel:
            code = _tag(r, "lapse_type")
            if code not in VOLuntary_LAPSE | {"STO", "PAU", "RED", "CON"}:
                continue
            if code in {"NON", "NA", "UNC", ""}:
                continue
            counts[code] += 1
            by_code[code].append(r)
        items = []
        for code, n in counts.most_common():
            sample = sorted(by_code[code], key=severity_score, reverse=True)
            quotes = [r.get("quote", "") for r in sample[:2] if r.get("quote")]
            pct = round(100.0 * n / rel_n, 2)
            branch = "lapse_type"
            label = expand_code(codebook, "lapse_type", code)
            sev = round(sum(severity_score(r) for r in by_code[code]) / n, 2)
            fixable = code in {"PAU", "CON", "RED"}
            score = round((n ** 0.5) * sev * (1.1 if fixable else 1.0), 2)
            by_src = Counter(r.get("source") or "unknown" for r in by_code[code])
            items.append(
                {
                    "label": label,
                    "code": code,
                    "posts": n,
                    "pct_of_relevant": pct,
                    "severity_avg": sev,
                    "metric_tree_branch": branch,
                    "fixable_under_guardrails": fixable,
                    "opportunity_score": score,
                    "quotes": quotes,
                    "by_source": dict(by_src),
                }
            )
        items.sort(key=lambda x: x["opportunity_score"], reverse=True)
        return items

    return {
        "causes": pack_causes(),
        "problems_after_stopping": pack_after_stop(),
        "usability_subthemes": build_usability_subthemes(rows),
        "denominator": "relevant_tagged_posts",
        "relevant_n": len(rel),
        "source_mix_relevant": dict(Counter(r.get("source") or "unknown" for r in rel)),
    }


def build_questions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rel = [r for r in rows if r.get("relevant")]
    for q in DISCOVERY_QUESTIONS:
        matched = [r for r in rel if q["match"](r)]
        n = len(matched)
        pct = round(100.0 * n / max(1, len(rel)), 2)
        by_year: Counter[int] = Counter()
        for r in matched:
            yr = r.get("year")
            if isinstance(yr, int):
                by_year[yr] += 1
        chart = [{"year": y, "posts": c} for y, c in sorted(by_year.items())]
        top_quotes = [
            {"id": r["id"], "quote": r.get("quote", ""), "source": r.get("source")}
            for r in sorted(matched, key=severity_score, reverse=True)[:3]
            if r.get("quote")
        ]
        numbers = [
            {"label": "Posts tagged for question", "value": n},
            {"label": "% of relevant corpus", "value": pct},
        ]
        if matched:
            numbers.append(
                {
                    "label": "Avg severity (1–5)",
                    "value": round(sum(severity_score(r) for r in matched) / n, 2),
                }
            )
        out.append(
            {
                "id": q["id"],
                "title": q["title"],
                "chart": chart,
                "numbers": numbers,
                "quotes": top_quotes,
                "post_count": n,
            }
        )
    return out


def build_metric_tree(rows: list[dict[str, Any]], codebook: dict[str, Any]) -> dict[str, Any]:
    rel = [r for r in rows if r.get("relevant")]
    branches: list[dict[str, Any]] = []
    for field, title in METRIC_BRANCHES:
        counts: Counter[str] = Counter()
        for r in rel:
            code = _tag(r, field)
            if code in {"", "NA"}:
                continue
            counts[code] += 1
        branches.append(
            {
                "field": field,
                "title": title,
                "codes": [
                    {
                        "code": c,
                        "label": expand_code(codebook, field, c),
                        "posts": n,
                    }
                    for c, n in counts.most_common()
                ],
            }
        )

    def vol_split(subset: list[dict[str, Any]]) -> dict[str, int]:
        c: Counter[str] = Counter()
        for r in subset:
            c[voluntary_bucket(r)] += 1
        return dict(c)

    by_source: dict[str, dict[str, int]] = {}
    for r in rel:
        src = r.get("source") or "unknown"
        if src not in by_source:
            by_source[src] = {"voluntary": 0, "involuntary": 0, "unclear": 0}
        by_source[src][voluntary_bucket(r)] += 1

    by_tagger: dict[str, dict[str, int]] = {}
    for r in rel:
        tb = r.get("tagged_by") or r.get("model") or "unknown"
        if tb not in by_tagger:
            by_tagger[tb] = {"voluntary": 0, "involuntary": 0, "unclear": 0}
        by_tagger[tb][voluntary_bucket(r)] += 1

    return {
        "root": "12-month active SIP (north star)",
        "branches": branches,
        "voluntary_vs_involuntary": {
            "overall": vol_split(rel),
            "by_source": by_source,
            "by_tagger": by_tagger,
        },
    }


def build_segments(rows: list[dict[str, Any]], codebook: dict[str, Any]) -> dict[str, Any]:
    rel = [r for r in rows if r.get("relevant")]
    lapse = [
        r
        for r in rel
        if _tag(r, "lapse_type") in VOLuntary_LAPSE | {"STO", "PAU", "RED", "CON"}
    ]
    groups: list[dict[str, Any]] = []
    seg_counts: Counter[str] = Counter()
    for r in rel:
        seg = _tag(r, "investor_segment")
        if seg in {"UNC", "NA", ""}:
            continue
        seg_counts[seg] += 1

    for seg, n in seg_counts.most_common():
        lapse_n = sum(1 for r in lapse if _tag(r, "investor_segment") == seg)
        groups.append(
            {
                "segment_code": seg,
                "segment_label": expand_code(codebook, "investor_segment", seg),
                "relevant_posts": n,
                "lapse_stories": lapse_n,
                "low_confidence": lapse_n < 15,
            }
        )

    by_source: dict[str, int] = Counter(r.get("source") or "unknown" for r in rel)
    return {
        "groups": groups,
        "source_mix": dict(by_source),
        "disclaimer": (
            "Age, income type, and money source are rarely stated in public posts; "
            "segment tags are proxies only—validate in interviews."
        ),
        "lapse_story_threshold": 15,
    }


def build_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if not verified_quote(r):
            continue
        out.append(
            {
                "id": r["id"],
                "source": r.get("source"),
                "year": r.get("year"),
                "relevant": bool(r.get("relevant")),
                "severity": severity_score(r),
                "quote": r.get("quote"),
                "tags": r.get("tags"),
                "tagged_by": r.get("tagged_by") or r.get("model"),
                "journey_stage": _tag(r, "journey_stage"),
                "lapse_type": _tag(r, "lapse_type"),
                "drop_trigger": _tag(r, "drop_trigger"),
                "is_curated_reddit": bool(r.get("is_curated_reddit")),
            }
        )
    out.sort(key=lambda x: (-x["severity"], -(x.get("year") or 0)))
    return out


def build_validation(tag_audit: dict[str, Any]) -> dict[str, Any]:
    inter = {}
    p = PROCESSED / "inter_model_agreement.json"
    if p.is_file():
        inter = json.loads(p.read_text(encoding="utf-8"))
    golden = REPO_ROOT / "data" / "manual" / "golden_set.jsonl"
    golden_n = 0
    if golden.is_file():
        golden_n = sum(1 for line in golden.read_text(encoding="utf-8").splitlines() if line.strip())
    corrections = load_corrections()
    labelling_path = REPO_ROOT / "data" / "manual" / "labelling_agreement.json"
    human_labelling: dict[str, Any] = {}
    if labelling_path.is_file():
        human_labelling = json.loads(labelling_path.read_text(encoding="utf-8"))
    return {
        "filter_prompt_version": "rules_fallback_v2 (export re-apply; GROQ unset)",
        "tag_prompt_version": "codebook_v1 + rules_engine v2",
        "tag_models": ["openai/gpt-oss-120b", "qwen/qwen3-32b", "rules_fallback"],
        "inter_model_agreement": inter,
        "golden_set_size": golden_n,
        "human_corrections": corrections,
        "human_corrections_path": "data/manual/corrections.csv",
        "human_labelling": human_labelling,
        "human_labelling_workbook": "data/review/labelling_sheet.xlsx",
        "human_labelling_manifest": "data/manual/labelling_sample.json",
        "ai_corrections": [],
        "tag_audit": tag_audit,
        "known_biases": [
            "Play Store reviews overweight app crashes and payment rage",
            "English-heavy corpus under-represents Hindi/Hinglish journeys",
            "Rules fallback tagging — re-run with GROQ for production labels",
            "Segment fields mostly UNC — do not treat as demographic facts",
            "Platform staff replies excluded via text heuristics (see export)",
        ],
    }


def build_overview(rows: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    by_source_counts = meta.get("counts", {}).get("by_source") or count_by_source(rows)
    years = [r.get("year") for r in rows if isinstance(r.get("year"), int)]
    return {
        "pipeline_diagram": [
            "Source connectors",
            "Normalize & dedupe",
            "Enrich (filter + tag)",
            "Structured store",
            "Analysis & export",
            "PM artifacts (this app)",
        ],
        "sources": [
            {
                "source": k,
                "tagged_posts": v["tagged"],
                "relevant_posts": v["relevant"],
            }
            for k, v in sorted(by_source_counts.items())
        ],
        "funnel": funnel_from_evidence(),
        "date_coverage": {
            "min_year": min(years) if years else None,
            "max_year": max(years) if years else None,
            "posts_from_2021": sum(1 for y in years if y >= 2021),
        },
        "models": meta.get("models", []),
        "source_mix_tagged": source_mix_line(by_source_counts, relevant=False),
        "source_mix_relevant": source_mix_line(by_source_counts, relevant=True),
    }


def embed_texts_local(texts: list[str]) -> np.ndarray:
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    array = np.asarray(list(model.embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return array / np.maximum(norms, 1e-12)


def build_ask_index(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], np.ndarray | None]:
    rel = [r for r in rows if r.get("relevant") and (r.get("text") or r.get("quote"))]
    rel = rel[:8000]
    if not rel:
        return [], None
    texts = [(r.get("text") or r.get("quote") or "")[:2000] for r in rel]
    corpus = [
        {
            "id": r["id"],
            "source": r.get("source"),
            "year": r.get("year"),
            "snippet": (r.get("quote") or texts[i])[:400],
        }
        for i, r in enumerate(rel)
    ]
    emb = embed_texts_local(texts)
    return corpus, emb


def run_export() -> None:
    EXPORT.mkdir(parents=True, exist_ok=True)
    codebook = load_codebook()
    tags = load_tag_rows()
    clean = load_clean_index()
    rows, staff_skipped = prepare_export_rows(tags, clean)
    tag_audit = run_tag_audit(rows)

    by_source = count_by_source(rows)
    no_reddit = filter_rows(rows, exclude_reddit=True)
    by_source_no_reddit = count_by_source(no_reddit)

    tagged_n = len(rows)
    relevant_n = sum(1 for r in rows if r.get("relevant"))

    meta = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data_as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tagged_count": tagged_n,
        "relevant_count": relevant_n,
        "counts": {
            "tagged": tagged_n,
            "relevant": relevant_n,
            "by_source": by_source,
            "exclude_reddit": {
                "tagged": len(no_reddit),
                "relevant": sum(1 for r in no_reddit if r.get("relevant")),
                "by_source": by_source_no_reddit,
            },
        },
        "excluded_staff_replies": staff_skipped,
        "definitions": {
            "tagged": (
                "Unique items with tag_status=ok after excluding platform staff replies "
                "(one record per id in this export)."
            ),
            "relevant": (
                "Tagged items where filter_relevant is 'yes' or tags.relevance_flag is 'Y' "
                "(SIP / mutual-fund discovery focus)."
            ),
        },
        "models": sorted({r.get("tagged_by") or r.get("model") or "unknown" for r in rows}),
        "codebook_version": codebook.get("version", "1.0"),
    }

    overview = build_overview(rows, meta)
    opportunities = build_opportunities(rows, codebook)
    questions = build_questions(rows)
    metric_tree = build_metric_tree(rows, codebook)
    segments = build_segments(rows, codebook)
    evidence = build_evidence(rows)
    validation = build_validation(tag_audit)

    (EXPORT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (EXPORT / "overview.json").write_text(json.dumps(overview, indent=2), encoding="utf-8")
    (EXPORT / "opportunities.json").write_text(json.dumps(opportunities, indent=2), encoding="utf-8")
    (EXPORT / "discovery_questions.json").write_text(json.dumps(questions, indent=2), encoding="utf-8")
    (EXPORT / "metric_tree.json").write_text(json.dumps(metric_tree, indent=2), encoding="utf-8")
    (EXPORT / "segments.json").write_text(json.dumps(segments, indent=2), encoding="utf-8")
    (EXPORT / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")

    with (EXPORT / "evidence.jsonl").open("w", encoding="utf-8") as handle:
        for row in evidence:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    ask_corpus, emb = build_ask_index(rows)
    with (EXPORT / "ask_corpus.jsonl").open("w", encoding="utf-8") as handle:
        for row in ask_corpus:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if emb is not None:
        np.savez_compressed(
            EXPORT / "ask_embeddings.npz",
            embeddings=emb,
            ids=[r["id"] for r in ask_corpus],
        )

    print(f"Export complete: {EXPORT}")
    print(
        f"  tagged={tagged_n} relevant={relevant_n} staff_excluded={staff_skipped} "
        f"evidence={len(evidence)} ask={len(ask_corpus)}"
    )


def main() -> int:
    run_export()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
