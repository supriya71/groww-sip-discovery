"""Groq LLM relevance filter on clean.jsonl. Resumable batches with rate-limit handling.

Run pilot (100 items, optional auto full run):
  python -m pipeline.filter --pilot

Full run (2021+, resumable):
  python -m pipeline.filter --full --min-year 2021 --auto-resume
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROCESSED_ROOT = REPO_ROOT / "data" / "processed"
CLEAN_PATH = PROCESSED_ROOT / "clean.jsonl"
RESULTS_PATH = PROCESSED_ROOT / "filter_results.jsonl"
STATE_PATH = PROCESSED_ROOT / "filter_state.json"

GROQ_BASE = "https://api.groq.com/openai/v1"
PREFERRED_MODELS = (
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "gpt-oss-20b",
    "gpt-oss-120b",
)
BATCH_SIZE = 25
TEXT_WORD_LIMIT = 150
CONTEXT_WORD_LIMIT = 25
DEFAULT_DAILY_TOKEN_LIMIT = 500_000  # fallback if headers missing

_FUND_HOUSES = (
    "hdfc",
    "sbi",
    "icici",
    "axis",
    "kotak",
    "nippon",
    "mirae",
    "parag parikh",
    "uti",
    "dsp",
    "franklin",
    "motilal",
    "invesco",
    "ppfas",
    "quant",
    "bandhan",
)
_SAFETY_TERMS = (
    "sip",
    "mutual fund",
    "mutual funds",
    "lumpsum",
    "lump sum",
    "mandate",
    "autopay",
    "auto pay",
    "auto-pay",
    "nav",
    "redemption",
    "redeem",
    "beginner",
    "first time",
    "first-time",
    "new investor",
) + _FUND_HOUSES
_FIRST_PERSON = re.compile(
    r"\b(i|i'm|i’ve|i'd|my|me|mine|we|our|mujhe|mera|meri|main|hum)\b",
    re.I,
)
_QUESTION = re.compile(r"\?|\b(how|why|what|when|where|can|should|kya|kaise)\b", re.I)

SYSTEM_PROMPT = """You classify user-generated text for SIP / mutual-fund discovery research in India.

Return ONLY valid JSON: {"results":[{"id":"<id>","relevant":"yes|no|unsure","reason":"<max 10 words>"}, ...]}

Rules:
- yes = experience, reason, question, problem, or feeling about starting, continuing, pausing, reducing, or stopping a SIP; new investor; mandates/autopay; NAV or returns confusion; redemption.
- no = stocks, F&O, IPOs, demat or bank issues with NO mutual fund angle; generic praise; spam.
- A confused or first-time investor is NEVER "no".
- When unsure between no and unsure, choose unsure.
- reason = up to 10 words, describe ONLY what the text says (no advice).

Worked examples:
1) id=a | text="My SIP paused after mandate failed twice" -> {"id":"a","relevant":"yes","reason":"SIP paused mandate failed twice"}
2) id=b | text="Great for intraday F&O only" -> {"id":"b","relevant":"no","reason":"F&O trading only"}
3) id=c | text="Nice app smooth UI" -> {"id":"c","relevant":"no","reason":"generic praise only"}
4) id=d | text="Is this ok for beginners long term?" -> {"id":"d","relevant":"unsure","reason":"vague beginner longterm question"}
"""


def truncate_words(text: str, limit: int) -> str:
    words = (text or "").split()
    if len(words) <= limit:
        return (text or "").strip()
    return " ".join(words[:limit])


def item_id(row: dict[str, Any]) -> str:
    for key in ("comment_id", "post_id", "review_id"):
        val = row.get(key)
        if val:
            return f"{row.get('source', 'unknown')}:{val}"
    digest = hashlib.sha256(
        f"{row.get('source')}:{row.get('text', '')}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{row.get('source', 'unknown')}:hash:{digest}"


def load_clean_rows(*, min_year: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in CLEAN_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if min_year is not None:
            year = row.get("year")
            if year is None or year < min_year:
                continue
        rows.append(row)
    return rows


def load_finished_ids() -> set[str]:
    if not RESULTS_PATH.is_file():
        return set()
    done: set[str] = set()
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("id"):
            done.add(rec["id"])
    return done


def load_results() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not RESULTS_PATH.is_file():
        return out
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("id"):
            out[rec["id"]] = rec
    return out


def groq_api_key() -> str:
    load_dotenv(REPO_ROOT / ".env")
    import os

    key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY is not set in .env (Groq console.groq.com — not the xAI GROK key)."
        )
    return key


def resolve_model(api_key: str) -> str:
    response = requests.get(
        f"{GROQ_BASE}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    available = {m["id"] for m in response.json().get("data", [])}
    for candidate in PREFERRED_MODELS:
        if candidate in available:
            return candidate
    for mid in sorted(available):
        if "oss" in mid.lower() or "gpt-oss" in mid.lower():
            return mid
    raise RuntimeError(
        f"None of {PREFERRED_MODELS} available. Sample models: {sorted(list(available))[:8]}"
    )


def parse_reset_seconds(reset_header: str | None) -> float | None:
    if not reset_header:
        return None
    reset_header = reset_header.strip()
    if reset_header.endswith("ms"):
        try:
            return max(0.0, int(reset_header[:-2]) / 1000.0)
        except ValueError:
            return None
    if reset_header.endswith("s"):
        try:
            return max(0.0, float(reset_header[:-1]))
        except ValueError:
            return None
    try:
        return max(0.0, float(reset_header))
    except ValueError:
        return None


def read_rate_limits(headers: Any) -> dict[str, Any]:
    def h(name: str) -> str | None:
        return headers.get(name) or headers.get(name.lower())

    return {
        "limit_requests": h("x-ratelimit-limit-requests"),
        "remaining_requests": h("x-ratelimit-remaining-requests"),
        "reset_requests": h("x-ratelimit-reset-requests"),
        "limit_tokens": h("x-ratelimit-limit-tokens"),
        "remaining_tokens": h("x-ratelimit-remaining-tokens"),
        "reset_tokens": h("x-ratelimit-reset-tokens"),
        "retry_after": h("retry-after"),
    }


def save_state(state: dict[str, Any]) -> None:
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_results(records: list[dict[str, Any]]) -> None:
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def safety_rescue(row: dict[str, Any], relevant: str) -> tuple[str, bool]:
    if relevant != "no":
        return relevant, False
    text = (row.get("text") or "").lower()
    if not any(term in text for term in _SAFETY_TERMS):
        return relevant, False
    if _FIRST_PERSON.search(text) or _QUESTION.search(text):
        return "unsure", True
    return relevant, False


def build_user_payload(batch: list[tuple[str, dict[str, Any]]]) -> str:
    items = []
    for iid, row in batch:
        items.append(
            {
                "id": iid,
                "source": row.get("source"),
                "year": row.get("year"),
                "text": truncate_words(row.get("text") or "", TEXT_WORD_LIMIT),
                "context": truncate_words(row.get("context") or "", CONTEXT_WORD_LIMIT)
                or None,
            }
        )
    return json.dumps({"items": items}, ensure_ascii=False)


def call_groq_batch(
    cfg: Any,
    model: str,
    batch: list[tuple[str, dict[str, Any]]],
    *,
    stats: dict[str, Any],
) -> list[dict[str, Any]]:
    from pipeline.llm_client import chat_json_batch

    user_content = (
        "Classify each item. Return JSON {\"results\":[...]} with one entry per id.\n"
        + build_user_payload(batch)
    )
    return chat_json_batch(cfg, model, SYSTEM_PROMPT, user_content, stats=stats)


def normalize_ai_result(
    batch: list[tuple[str, dict[str, Any]]],
    ai_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(r.get("id")): r for r in ai_results if r.get("id") is not None}
    out: list[dict[str, Any]] = []
    for iid, row in batch:
        raw = by_id.get(iid, {})
        relevant = str(raw.get("relevant", "unsure")).lower().strip()
        if relevant not in {"yes", "no", "unsure"}:
            relevant = "unsure"
        reason = " ".join(str(raw.get("reason", "")).split()[:10])
        relevant, rescued = safety_rescue(row, relevant)
        out.append(
            {
                "id": iid,
                "source": row.get("source"),
                "year": row.get("year"),
                "relevant": relevant,
                "reason": reason,
                "rescued_by_rule": rescued,
                "model_relevant": str(raw.get("relevant", "")).lower(),
                "filtered_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return out


def daily_limit_reached(limits: dict[str, Any]) -> bool:
    for key in ("remaining_tokens", "remaining_requests"):
        val = limits.get(key)
        if val is None:
            continue
        try:
            if int(float(val)) <= 0:
                return True
        except ValueError:
            continue
    return False


def wait_for_reset(limits: dict[str, Any], *, auto_resume: bool) -> bool:
    if not daily_limit_reached(limits):
        return True
    reset_s = parse_reset_seconds(limits.get("reset_tokens")) or parse_reset_seconds(
        limits.get("reset_requests")
    )
    if not auto_resume:
        return False
    if reset_s is None:
        reset_s = 3600.0
    print(f"Daily/quota limit hit; sleeping {reset_s:.0f}s for reset (--auto-resume)…")
    time.sleep(reset_s + 1)
    return True


def stratified_sample(rows: list[dict[str, Any]], n: int, seed: int = 42) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[row.get("source") or "unknown"].append(row)
    sources = sorted(by_source)
    if not sources:
        return []
    rng = random.Random(seed)
    picked: list[dict[str, Any]] = []
    per = max(1, n // len(sources))
    for src in sources:
        pool = by_source[src][:]
        rng.shuffle(pool)
        picked.extend(pool[:per])
    remaining = n - len(picked)
    if remaining > 0:
        rest = [r for r in rows if r not in picked]
        rng.shuffle(rest)
        picked.extend(rest[:remaining])
    rng.shuffle(picked)
    return picked[:n]


def run_filter(
    rows: list[dict[str, Any]],
    *,
    cfg: Any,
    model: str,
    auto_resume: bool,
    run_label: str,
) -> dict[str, Any]:
    finished = load_finished_ids()
    pending = [r for r in rows if item_id(r) not in finished]
    stats: dict[str, Any] = {
        "run_label": run_label,
        "model": model,
        "pending": len(pending),
        "already_done": len(finished),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "requests": 0,
        "processed_this_run": 0,
    }
    save_state({"run_label": run_label, "started_at": datetime.now(timezone.utc).isoformat(), **stats})

    idx = 0
    while idx < len(pending):
        batch_rows = pending[idx : idx + BATCH_SIZE]
        batch = [(item_id(r), r) for r in batch_rows]
        try:
            ai_results = call_groq_batch(cfg, model, batch, stats=stats)
        except RuntimeError as exc:
            limits = stats.get("last_rate_limits") or {}
            if daily_limit_reached(limits) and wait_for_reset(limits, auto_resume=auto_resume):
                continue
            raise exc

        records = normalize_ai_result(batch, ai_results)
        append_results(records)
        stats["processed_this_run"] += len(records)
        idx += BATCH_SIZE

        limits = stats.get("last_rate_limits") or {}
        save_state({**stats, "last_rate_limits": limits, "updated_at": datetime.now(timezone.utc).isoformat()})

        if daily_limit_reached(limits):
            print("Quota limit reached (per Groq response headers).")
            if not wait_for_reset(limits, auto_resume=auto_resume):
                break

    stats["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_state(stats)
    return stats


def clearly_wrong_no(rec: dict[str, Any], row: dict[str, Any]) -> bool:
    """Heuristic: model said no but text looks MF/SIP-first-person/question."""
    if rec.get("relevant") != "no":
        return False
    if rec.get("rescued_by_rule"):
        return False
    upgraded, rescued = safety_rescue(row, "no")
    return rescued or upgraded == "unsure"


def print_pilot_report(
    pilot_rows: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    stats: dict[str, Any],
    *,
    total_full: int,
) -> int:
    by_source: Counter[str] = Counter()
    verdicts: Counter[str] = Counter()
    for row in pilot_rows:
        iid = item_id(row)
        rec = results.get(iid)
        if not rec:
            continue
        by_source[row.get("source") or "unknown"] += 1
        verdicts[rec["relevant"]] += 1

    print("\n=== PILOT relevance (100 items) ===")
    print(f"Model: {stats.get('model')}")
    print("Verdicts:", dict(verdicts))
    print("\nPer source (pilot sample):")
    for src in sorted(by_source):
        sub = Counter(
            results[item_id(r)]["relevant"]
            for r in pilot_rows
            if (r.get("source") or "unknown") == src and item_id(r) in results
        )
        print(f"  {src}: {dict(sub)}")

    yes_samples = [
        (results[item_id(r)], r)
        for r in pilot_rows
        if item_id(r) in results and results[item_id(r)]["relevant"] == "yes"
    ]
    no_samples = [
        (results[item_id(r)], r)
        for r in pilot_rows
        if item_id(r) in results and results[item_id(r)]["relevant"] == "no"
    ]
    random.shuffle(yes_samples)
    random.shuffle(no_samples)

    print("\n10 YES (reason + text snippet):")
    for rec, row in yes_samples[:10]:
        print(f"  [{row.get('source')}] {rec['reason']} | {(row.get('text') or '')[:100]}")

    print("\n10 NO (reason + text snippet):")
    for rec, row in no_samples[:10]:
        print(f"  [{row.get('source')}] {rec['reason']} | {(row.get('text') or '')[:100]}")

    tokens = int(stats.get("total_tokens") or 0)
    per_item = tokens / max(1, stats.get("processed_this_run", 1))
    daily_cap = DEFAULT_DAILY_TOKEN_LIMIT
    lim = stats.get("last_rate_limits") or {}
    if lim.get("limit_tokens"):
        try:
            daily_cap = int(float(lim["limit_tokens"]))
        except ValueError:
            pass
    est_days = (per_item * total_full) / daily_cap if daily_cap else 0
    print(f"\nTokens this pilot: {tokens} (~{per_item:.0f}/item)")
    print(f"Estimated full run ({total_full} items, {daily_cap} token/day cap): ~{est_days:.1f} days")

    wrong = sum(
        1
        for row in pilot_rows
        if item_id(row) in results and clearly_wrong_no(results[item_id(row)], row)
    )
    print(f"\nClearly wrong NO (heuristic): {wrong} / {verdicts.get('no', 0)} no items")
    return wrong


def main() -> int:
    parser = argparse.ArgumentParser(description="Groq relevance filter")
    parser.add_argument("--pilot", action="store_true", help="Run 100-item pilot")
    parser.add_argument("--full", action="store_true", help="Run full filter pass")
    parser.add_argument("--min-year", type=int, default=2021, help="Min year for full run")
    parser.add_argument(
        "--auto-resume",
        action="store_true",
        help="Wait for Groq rate-limit reset and continue",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-auto-full",
        action="store_true",
        help="Pilot only: do not start full run even if gate passes",
    )
    args = parser.parse_args()

    if not CLEAN_PATH.is_file():
        print(f"Missing {CLEAN_PATH}; run pipeline.clean first.")
        return 1

    try:
        from pipeline.llm_client import load_llm_config, resolve_filter_model

        cfg = load_llm_config()
        model = resolve_filter_model(cfg)
    except (RuntimeError, Exception) as exc:
        print(exc)
        return 1
    print(f"Using LLM provider={cfg.provider} model={model}")

    all_rows = load_clean_rows()
    full_rows = load_clean_rows(min_year=args.min_year)

    if args.pilot:
        pilot_rows = stratified_sample(all_rows, 100, seed=args.seed)
        stats = run_filter(
            pilot_rows,
            cfg=cfg,
            model=model,
            auto_resume=args.auto_resume,
            run_label="pilot_100",
        )
        results = load_results()
        wrong = print_pilot_report(pilot_rows, results, stats, total_full=len(full_rows))
        if wrong <= 2 and not args.no_auto_full:
            print("\nPilot gate passed (≤2 clearly wrong NO). Starting full run…")
            run_filter(
                full_rows,
                cfg=cfg,
                model=model,
                auto_resume=args.auto_resume,
                run_label=f"full_{args.min_year}_plus",
            )
        elif wrong <= 2:
            print("\nPilot gate passed; full run skipped (--no-auto-full).")
        else:
            print("\nPilot gate FAILED (>2 clearly wrong NO). Full run not started — review samples above.")
        return 0

    if args.full:
        run_filter(
            full_rows,
            cfg=cfg,
            model=model,
            auto_resume=args.auto_resume,
            run_label=f"full_{args.min_year}_plus",
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
