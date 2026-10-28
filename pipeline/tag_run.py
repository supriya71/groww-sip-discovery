"""Dual-model tagging run: inter-model check, priority queue, parallel Qwen.

  python -m pipeline.tag_run [--auto-resume] [--inter-only]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EVIDENCE_LOG = REPO_ROOT / "docs" / "evidence_log_notes.md"
INTER_MODEL_PATH = REPO_ROOT / "data" / "processed" / "inter_model_agreement.json"
TAG_QUEUE_PATH = REPO_ROOT / "data" / "processed" / "tag_queue.jsonl"

from pipeline.filter import item_id, load_clean_rows  # noqa: E402
from pipeline.rules_engine import tag_item  # noqa: E402
from pipeline.llm_client import (  # noqa: E402
    load_groq_config,
    resolve_qwen_model,
    resolve_tag_model,
)
from pipeline.tag import (  # noqa: E402
    BATCH_SIZE,
    PROCESSED_ROOT,
    TAG_RESULTS_PATH,
    _TAG_FIELDS,
    build_system_prompt,
    load_codebook,
    load_tag_finished_keys,
    run_tagging,
    stratified_sample,
)

PRIMARY_TAGGED_BY = "openai/gpt-oss-120b"
QWEN_TAGGED_BY = "qwen/qwen3-32b"

_PRIORITY_RE = re.compile(
    r"\b("
    r"stop|stopped|pause|paused|cancel|cancelled|canceled|band|skip|skipped|"
    r"fail|failed|failure|bounce|bounced|mandate|autopay|auto-pay|auto pay|"
    r"loss|losses|negative|redeem|redemption"
    r")\b",
    re.I,
)
# "red" only as whole word (avoid "credit", "shared")
_RED_RE = re.compile(r"\bred\b", re.I)


def is_priority_text(text: str) -> bool:
    if not text:
        return False
    if _PRIORITY_RE.search(text):
        return True
    return bool(_RED_RE.search(text))


def synth_filter_rec(row: dict[str, Any], *, priority: bool) -> dict[str, Any]:
    text = (row.get("text") or "").lower()
    if priority or "sip" in text or "mutual fund" in text:
        return {"relevant": "yes"}
    return {"relevant": "unsure"}


def load_rows_for_tagging() -> list[dict[str, Any]]:
    if TAG_QUEUE_PATH.is_file():
        rows: list[dict[str, Any]] = []
        for line in TAG_QUEUE_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows
    return list(load_clean_rows())


def reddit_assigned_tagger_map() -> dict[str, str]:
    if not TAG_QUEUE_PATH.is_file():
        return {}
    mapping: dict[str, str] = {}
    for row in load_rows_for_tagging():
        if row.get("source") != "reddit":
            continue
        assigned = row.get("assigned_tagger")
        if assigned:
            mapping[item_id(row)] = str(assigned)
    return mapping


def all_clean_items() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for row in load_rows_for_tagging():
        pri = is_priority_text(row.get("text") or "")
        items.append((item_id(row), row, synth_filter_rec(row, priority=pri)))
    return items


def load_tag_index() -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    if not TAG_RESULTS_PATH.is_file():
        return index
    for line in TAG_RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("tag_status") != "ok":
            continue
        key = (rec["id"], rec.get("tagged_by") or rec.get("model", ""))
        index[key] = rec
    return index


def inter_model_sample(
    items: list[tuple[str, dict[str, Any], dict[str, Any]]],
    n: int = 30,
    seed: int = 7,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    priority = [e for e in items if is_priority_text(e[1].get("text") or "")]
    pool = priority if len(priority) >= n else items
    return stratified_sample(pool, n, seed=seed)


def agreement_report(
    sample_ids: list[str],
    primary_by: str,
    qwen_by: str,
) -> dict[str, Any]:
    index = load_tag_index()
    report: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "n_items": len(sample_ids),
        "primary_tagged_by": primary_by,
        "qwen_tagged_by": qwen_by,
        "field_agreement": {},
        "overall": {},
    }
    field_stats: dict[str, dict[str, Any]] = {}
    for field in _TAG_FIELDS:
        agree = 0
        total = 0
        for iid in sample_ids:
            a = index.get((iid, primary_by))
            b = index.get((iid, qwen_by))
            if not a or not b:
                continue
            total += 1
            if a.get("tags", {}).get(field) == b.get("tags", {}).get(field):
                agree += 1
        pct = round(100.0 * agree / total, 1) if total else 0.0
        field_stats[field] = {"agree": agree, "total": total, "pct": pct}
    report["field_agreement"] = field_stats
    if field_stats:
        avg = sum(v["pct"] for v in field_stats.values()) / len(field_stats)
        report["overall"] = {"mean_field_agreement_pct": round(avg, 1)}
    return report


def print_agreement(report: dict[str, Any]) -> None:
    print("\n=== INTER-MODEL CHECK (same 30 items) ===")
    print(f"Primary: {report['primary_tagged_by']}")
    print(f"Qwen:    {report['qwen_tagged_by']}")
    for field, stats in report["field_agreement"].items():
        print(f"  {field}: {stats['agree']}/{stats['total']} ({stats['pct']}%)")
    print(f"Mean field agreement: {report.get('overall', {}).get('mean_field_agreement_pct')}%")
    print(f"Wrote {INTER_MODEL_PATH}")


def append_evidence_inter_model(report: dict[str, Any]) -> None:
    lines = [
        "",
        "## Inter-model tagging check (30 shared items)",
        "",
        f"- Primary: `{report['primary_tagged_by']}` vs Qwen: `{report['qwen_tagged_by']}`",
        f"- Mean field agreement: **{report.get('overall', {}).get('mean_field_agreement_pct')}%**",
        "",
        "| Field | Agree | Total | % |",
        "| --- | ---: | ---: | ---: |",
    ]
    for field, stats in report["field_agreement"].items():
        lines.append(f"| {field} | {stats['agree']} | {stats['total']} | {stats['pct']} |")
    lines.extend(
        [
            "",
            "### Segment fields (`investor_segment`)",
            "- Public posts rarely state income type, age band, or influence source explicitly.",
            "- **`investor_segment` is often `UNC` in store/forum text**; treat segment labels as **proxies** only.",
            "- **Validate segment hypotheses in user interviews**, not from tag counts alone.",
            "",
        ]
    )
    existing = EVIDENCE_LOG.read_text(encoding="utf-8") if EVIDENCE_LOG.is_file() else "# Evidence log\n"
    EVIDENCE_LOG.write_text(existing + "\n".join(lines), encoding="utf-8")


def rules_tag_items(
    items: list[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    tagged_by: str,
    model: str,
) -> list[dict[str, Any]]:
    from pipeline.tag import (
        apply_quote_checks,
        expand_codes,
        load_codebook,
        reason_hallucination_flag,
    )

    codebook = load_codebook()
    finished = load_tag_finished_keys()
    records: list[dict[str, Any]] = []
    for iid, row, frec in items:
        if (iid, tagged_by) in finished:
            continue
        raw = tag_item(row, frec)
        text = row.get("text") or ""
        quote_meta = apply_quote_checks(text, raw.get("quote"))
        records.append(
            {
                "id": iid,
                "source": row.get("source"),
                "year": row.get("year"),
                "filter_relevant": frec.get("relevant"),
                "tag_status": "ok",
                "tags": {field: str(raw.get(field)) for field in _TAG_FIELDS},
                "tags_expanded": expand_codes(raw, codebook),
                "quote": quote_meta.get("quote"),
                "quote_original": quote_meta.get("quote_original"),
                "quote_repaired": quote_meta.get("quote_repaired"),
                "quote_hidden": quote_meta.get("quote_hidden"),
                "quote_match_score": quote_meta.get("quote_match_score"),
                "tag_reason": raw.get("tag_reason", ""),
                "reason_hallucination_flag": reason_hallucination_flag(
                    text, raw.get("tag_reason", "")
                ),
                "tagged_at": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "tagged_by": tagged_by,
                "pilot_mode": "rules_fallback",
            }
        )
    return records


def run_rules_worker(
    items: list[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    tagged_by: str,
    model: str,
) -> int:
    from pipeline.tag import append_tag_results

    batch: list = []
    count = 0
    for entry in items:
        batch.append(entry)
        if len(batch) >= BATCH_SIZE:
            recs = rules_tag_items(batch, tagged_by=tagged_by, model=model)
            if recs:
                append_tag_results(recs)
                count += len(recs)
            batch = []
    if batch:
        recs = rules_tag_items(batch, tagged_by=tagged_by, model=model)
        if recs:
            append_tag_results(recs)
            count += len(recs)
    return count


def run_worker(
    items: list[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    cfg: Any,
    model: str,
    tagged_by: str,
    system_prompt: str,
    codebook: dict[str, Any],
    auto_resume: bool,
    run_label: str,
) -> dict[str, Any]:
    return run_tagging(
        items,
        cfg=cfg,
        model=model,
        tagged_by=tagged_by,
        system_prompt=system_prompt,
        codebook=codebook,
        auto_resume=auto_resume,
        run_label=run_label,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Dual-model tagging run")
    parser.add_argument("--auto-resume", action="store_true")
    parser.add_argument("--inter-only", action="store_true", help="Only run 30-item inter-model check")
    parser.add_argument("--rules-fallback", action="store_true", help="Use rules if GROQ missing")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    use_rules = args.rules_fallback
    cfg = None
    primary_model = PRIMARY_TAGGED_BY
    qwen_model = QWEN_TAGGED_BY
    try:
        cfg = load_groq_config()
        primary_model = resolve_tag_model(cfg)
        qwen_model = resolve_qwen_model(cfg)
    except RuntimeError as exc:
        if not args.rules_fallback:
            print(f"{exc}")
            print("Re-run with --rules-fallback to proceed without Groq, or set GROQ_API_KEY.")
            return 1
        use_rules = True
        print(f"LLM unavailable ({exc}); using --rules-fallback.")

    primary_tagged_by = primary_model
    qwen_tagged_by = qwen_model
    print(f"Primary tagger: {primary_model}")
    print(f"Qwen tagger:    {qwen_model}")

    codebook = load_codebook()
    system_prompt = build_system_prompt(codebook) if not use_rules else ""
    all_items = all_clean_items()
    priority_items = [e for e in all_items if is_priority_text(e[1].get("text") or "")]
    print(f"Clean items: {len(all_items)} | priority keyword matches: {len(priority_items)}")

    sample_30 = inter_model_sample(all_items, 30, seed=args.seed)
    sample_ids = [iid for iid, _, _ in sample_30]

    print("\nPhase 1: inter-model check (30 shared items)…")
    if use_rules:
        n1 = run_rules_worker(sample_30, tagged_by=primary_tagged_by, model=primary_model)
        n2 = run_rules_worker(sample_30, tagged_by=qwen_tagged_by, model=qwen_model)
        print(f"  rules primary={n1} qwen={n2} rows")
    else:
        run_worker(
            sample_30,
            cfg=cfg,
            model=primary_model,
            tagged_by=primary_tagged_by,
            system_prompt=system_prompt,
            codebook=codebook,
            auto_resume=args.auto_resume,
            run_label="inter_model_primary",
        )
        run_worker(
            sample_30,
            cfg=cfg,
            model=qwen_model,
            tagged_by=qwen_tagged_by,
            system_prompt=system_prompt,
            codebook=codebook,
            auto_resume=args.auto_resume,
            run_label="inter_model_qwen",
        )
    report = agreement_report(sample_ids, primary_tagged_by, qwen_tagged_by)
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    INTER_MODEL_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_agreement(report)
    append_evidence_inter_model(report)

    if args.inter_only:
        return 0

    reddit_assign = reddit_assigned_tagger_map()
    finished = load_tag_finished_keys()
    pri_pending = [
        e
        for e in priority_items
        if (e[0], primary_tagged_by) not in finished
        and reddit_assign.get(e[0], primary_tagged_by) == primary_tagged_by
    ]
    print(f"\nPhase 2: primary tags {len(pri_pending)} priority items (no filter gate)…")
    if pri_pending:
        if use_rules:
            n = run_rules_worker(pri_pending, tagged_by=primary_tagged_by, model=primary_model)
            print(f"  priority tagged this run: {n}")
        else:
            stats_pri = run_worker(
                pri_pending,
                cfg=cfg,
                model=primary_model,
                tagged_by=primary_tagged_by,
                system_prompt=system_prompt,
                codebook=codebook,
                auto_resume=args.auto_resume,
                run_label="priority_primary",
            )
            print(f"  priority tagged this run: {stats_pri.get('processed_this_run')}")

    finished = load_tag_finished_keys()
    inter_ids = set(sample_ids)
    pri_ids = {e[0] for e in priority_items}
    reddit_primary: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    reddit_qwen: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    rest_pool: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for e in all_items:
        iid = e[0]
        if iid in inter_ids or iid in pri_ids:
            continue
        assigned = reddit_assign.get(iid)
        if assigned == primary_tagged_by:
            if (iid, primary_tagged_by) not in finished:
                reddit_primary.append(e)
            continue
        if assigned == qwen_tagged_by:
            if (iid, qwen_tagged_by) not in finished:
                reddit_qwen.append(e)
            continue
        if assigned:
            continue
        if (iid, primary_tagged_by) not in finished and (iid, qwen_tagged_by) not in finished:
            rest_pool.append(e)
    random.Random(args.seed).shuffle(rest_pool)
    mid = len(rest_pool) // 2
    primary_rest = reddit_primary + rest_pool[:mid]
    qwen_rest = reddit_qwen + rest_pool[mid:]
    print(
        f"\nPhase 3: parallel tagging — primary {len(primary_rest)} "
        f"(reddit {len(reddit_primary)}), qwen {len(qwen_rest)} (reddit {len(reddit_qwen)})"
    )

    if use_rules:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(
                run_rules_worker,
                primary_rest,
                tagged_by=primary_tagged_by,
                model=primary_model,
            )
            f2 = pool.submit(
                run_rules_worker,
                qwen_rest,
                tagged_by=qwen_tagged_by,
                model=qwen_model,
            )
            print(f"  primary done: {f1.result()} rows")
            print(f"  qwen done: {f2.result()} rows")
    else:
        results: dict[str, dict[str, Any]] = {}

        def _primary_job() -> dict[str, Any]:
            return run_worker(
                primary_rest,
                cfg=cfg,
                model=primary_model,
                tagged_by=primary_tagged_by,
                system_prompt=system_prompt,
                codebook=codebook,
                auto_resume=args.auto_resume,
                run_label="parallel_primary",
            )

        def _qwen_job() -> dict[str, Any]:
            return run_worker(
                qwen_rest,
                cfg=cfg,
                model=qwen_model,
                tagged_by=qwen_tagged_by,
                system_prompt=system_prompt,
                codebook=codebook,
                auto_resume=args.auto_resume,
                run_label="parallel_qwen",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(_primary_job): "primary",
                pool.submit(_qwen_job): "qwen",
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    results[name] = fut.result()
                    print(
                        f"  {name} done: processed={results[name].get('processed_this_run')} "
                        f"tokens={results[name].get('total_tokens')}"
                    )
                except Exception as exc:
                    print(f"  {name} failed: {exc}")
                    return 1

    print("\nTagging run complete. See tag_results.jsonl (tagged_by) and inter_model_agreement.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
