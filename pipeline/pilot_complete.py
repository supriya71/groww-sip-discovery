"""Run filter + tag pilots end-to-end (LLM if configured, else rules fallback).

  python -m pipeline.pilot_complete
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.filter import (  # noqa: E402
    PROCESSED_ROOT,
    RESULTS_PATH as FILTER_RESULTS_PATH,
    append_results,
    item_id,
    load_clean_rows,
    load_finished_ids,
    print_pilot_report,
    run_filter,
    safety_rescue,
    stratified_sample,
)
from pipeline.rules_engine import filter_item, tag_item  # noqa: E402
from pipeline.tag import (  # noqa: E402
    TAG_RESULTS_PATH,
    _TAG_FIELDS,
    append_tag_results,
    apply_quote_checks,
    expand_codes,
    load_codebook,
    load_eligible_items,
    load_tag_finished_ids,
    print_pilot_report as print_tag_pilot_report,
    reason_hallucination_flag,
    run_tagging,
    stratified_sample as tag_stratified_sample,
)


def rules_filter_pilot(seed: int = 42) -> None:
    finished = load_finished_ids()
    pilot_rows = stratified_sample(load_clean_rows(), 100, seed=seed)
    records = []
    for row in pilot_rows:
        iid = item_id(row)
        if iid in finished:
            continue
        verdict = filter_item(row)
        rel = verdict["relevant"]
        rel, rescued = safety_rescue(row, rel)
        records.append(
            {
                "id": iid,
                "source": row.get("source"),
                "year": row.get("year"),
                "relevant": rel,
                "reason": verdict["reason"],
                "rescued_by_rule": rescued,
                "model_relevant": verdict["relevant"],
                "filtered_at": datetime.now(timezone.utc).isoformat(),
                "pilot_mode": "rules_fallback",
            }
        )
    if records:
        append_results(records)
    print(f"Rules filter pilot: wrote {len(records)} rows to {FILTER_RESULTS_PATH}")


def rules_tag_pilot(seed: int = 42) -> None:
    codebook = load_codebook()
    finished = load_tag_finished_ids()
    eligible = load_eligible_items()
    pilot_items = tag_stratified_sample(eligible, 50, seed=seed)
    records = []
    for iid, row, frec in pilot_items:
        if iid in finished:
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
                "tags": {f: str(raw.get(f)) for f in _TAG_FIELDS},
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
                "model": "rules_fallback",
                "tagged_by": "rules_fallback",
                "pilot_mode": "rules_fallback",
            }
        )
    if records:
        append_tag_results(records)
    print(f"Rules tag pilot: wrote {len(records)} rows to {TAG_RESULTS_PATH}")


def try_llm_filter_pilot(auto_resume: bool, no_auto_full: bool, seed: int) -> bool:
    try:
        from pipeline.llm_client import load_llm_config, resolve_filter_model
        from pipeline.filter import load_results

        cfg = load_llm_config()
        model = resolve_filter_model(cfg)
    except Exception as exc:
        print(f"LLM filter unavailable: {exc}")
        return False

    print(f"LLM filter pilot: provider={cfg.provider} model={model}")
    pilot_rows = stratified_sample(load_clean_rows(), 100, seed=seed)
    stats = run_filter(
        pilot_rows,
        cfg=cfg,
        model=model,
        auto_resume=auto_resume,
        run_label="pilot_100",
    )
    full_rows = [r for r in load_clean_rows() if (r.get("year") or 0) >= 2021]
    wrong = print_pilot_report(pilot_rows, load_results(), stats, total_full=len(full_rows))
    if wrong <= 2 and not no_auto_full:
        print("(Full filter run skipped in pilot_complete; use pipeline.filter --full)")
    return True


def try_llm_tag_pilot(auto_resume: bool, seed: int) -> bool:
    try:
        from pipeline.llm_client import load_llm_config, resolve_tag_model
        from pipeline.tag import build_system_prompt, load_codebook, run_tagging

        cfg = load_llm_config()
        model = resolve_tag_model(cfg)
        codebook = load_codebook()
    except Exception as exc:
        print(f"LLM tag unavailable: {exc}")
        return False

    eligible = load_eligible_items()
    if not eligible:
        return False
    pilot_items = tag_stratified_sample(eligible, 50, seed=seed)
    print(f"LLM tag pilot: provider={cfg.provider} model={model}")
    stats = run_tagging(
        pilot_items,
        cfg=cfg,
        model=model,
        tagged_by=model,
        system_prompt=build_system_prompt(codebook),
        codebook=codebook,
        auto_resume=auto_resume,
        run_label="tag_pilot_50",
    )
    print_tag_pilot_report(pilot_items, stats, total_eligible=len(eligible))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Complete filter + tag pilots")
    parser.add_argument("--auto-resume", action="store_true")
    parser.add_argument("--no-auto-full", action="store_true", default=True)
    parser.add_argument("--rules-only", action="store_true", help="Skip LLM; use rules fallback")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)

    used_llm = False
    if not args.rules_only:
        used_llm = try_llm_filter_pilot(args.auto_resume, args.no_auto_full, args.seed)
    if not used_llm:
        print("Using rules-based filter pilot (set GROQ_API_KEY for LLM).")
        rules_filter_pilot(args.seed)
        from pipeline.filter import load_results

        pilot_rows = stratified_sample(load_clean_rows(), 100, seed=args.seed)
        stats = {"model": "rules_fallback", "total_tokens": 0, "processed_this_run": 100, "last_rate_limits": {}}
        full_rows = [r for r in load_clean_rows() if (r.get("year") or 0) >= 2021]
        print_pilot_report(pilot_rows, load_results(), stats, total_full=len(full_rows))

    if not FILTER_RESULTS_PATH.is_file():
        print("No filter results produced.")
        return 1

    used_llm_tag = False
    if not args.rules_only:
        used_llm_tag = try_llm_tag_pilot(args.auto_resume, args.seed)
    if not used_llm_tag:
        print("Using rules-based tag pilot (set GROQ_API_KEY for LLM).")
        rules_tag_pilot(args.seed)

    eligible = load_eligible_items()
    pilot_items = tag_stratified_sample(eligible, 50, seed=args.seed)
    stats = {
        "model": "rules_fallback" if not used_llm_tag else "llm",
        "processed_this_run": len(pilot_items),
        "total_tokens": 0,
        "last_rate_limits": {},
    }
    print_tag_pilot_report(pilot_items, stats, total_eligible=len(eligible))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
