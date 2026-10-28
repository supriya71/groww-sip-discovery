"""Groq structured tagging for filter yes/unsure items (codebook-driven).

Pilot only (50 items):
  python -m pipeline.tag --pilot [--auto-resume]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from rapidfuzz import fuzz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.filter import (  # noqa: E402
    CLEAN_PATH,
    DEFAULT_DAILY_TOKEN_LIMIT,
    GROQ_BASE,
    PROCESSED_ROOT,
    daily_limit_reached,
    groq_api_key,
    item_id,
    load_clean_rows,
    parse_reset_seconds,
    read_rate_limits,
    truncate_words,
)

CODEBOOK_PATH = REPO_ROOT / "codebook.yaml"
FILTER_RESULTS_PATH = PROCESSED_ROOT / "filter_results.jsonl"
TAG_RESULTS_PATH = PROCESSED_ROOT / "tag_results.jsonl"
TAG_STATE_PATH = PROCESSED_ROOT / "tag_state.json"
_TAG_WRITE_LOCK = threading.Lock()
_TAG_STATE_LOCK = threading.Lock()

TAG_MODEL_PREFERRED = (
    "openai/gpt-oss-120b",
    "gpt-oss-120b",
)
BATCH_SIZE = 20
TEXT_WORD_LIMIT = 150
CONTEXT_WORD_LIMIT = 25
QUOTE_MIN_SCORE = 80

_REASON_HALLMARKS = (
    ("sip", ("sip",)),
    ("mandate", ("mandate", "autopay", "auto-debit", "auto debit")),
    ("returns", ("return", "returns", "xirr", "profit", "loss", "nav")),
)

_TAG_FIELDS = (
    "journey_stage",
    "lapse_type",
    "drop_trigger",
    "expectation",
    "in_app_friction",
    "investor_segment",
    "sentiment",
    "relevance_flag",
)


def load_codebook() -> dict[str, Any]:
    with CODEBOOK_PATH.open(encoding="utf-8") as handle:
        book = yaml.safe_load(handle)
    if not isinstance(book, dict) or "codes" not in book:
        raise ValueError(f"Invalid codebook: {CODEBOOK_PATH}")
    return book


def build_system_prompt(codebook: dict[str, Any]) -> str:
    yaml_block = CODEBOOK_PATH.read_text(encoding="utf-8")
    return f"""You tag Indian SIP / mutual-fund user text for discovery research.

The CODEBOOK below is fixed — use ONLY short codes listed under codes.* for each field.

CODEBOOK (yaml):
```yaml
{yaml_block}```

Output ONLY valid JSON: {{"results":[{{...}}, ...]}} — one object per input id.

Each result object MUST include:
- id (string, matches input)
- journey_stage, lapse_type, drop_trigger, expectation, in_app_friction, investor_segment, sentiment, relevance_flag (short codes)
- quote: one continuous span copied EXACTLY from the item text (no "..." truncation)
- tag_reason: up to 10 words describing what the text states or implies

Rules:
- Tag what the text states OR plainly implies. Use UNC (unclear) only when the text gives nothing for that field.
- relevance_flag is Y or U only (from input filter_relevant). Still fill ALL other fields.
- quote must appear verbatim in text; pick the most informative span.

Worked examples (distinct item numbers):
Item 101 | filter_relevant=yes | text="Started 500 SIP but mandate failed twice" ->
{{"id":"101","journey_stage":"S1","lapse_type":"CON","drop_trigger":"MD","expectation":"PS","in_app_friction":"PM","investor_segment":"UNC","sentiment":"NEG","relevance_flag":"Y","quote":"mandate failed twice","tag_reason":"new SIP mandate failing"}}

Item 102 | filter_relevant=unsure | text="Is Groww ok for beginners?" ->
{{"id":"102","journey_stage":"S0","lapse_type":"NA","drop_trigger":"NA","expectation":"UNC","in_app_friction":"NA","investor_segment":"YNG","sentiment":"NEU","relevance_flag":"U","quote":"ok for beginners","tag_reason":"beginner suitability question"}}

Item 103 | filter_relevant=yes | text="Paused SIP after market crash fear" ->
{{"id":"103","journey_stage":"S3","lapse_type":"PAU","drop_trigger":"MF","expectation":"TM","in_app_friction":"NA","investor_segment":"UNC","sentiment":"NEG","relevance_flag":"Y","quote":"Paused SIP after market crash fear","tag_reason":"paused SIP market fear"}}

Item 104 | filter_relevant=yes | text="Nice smooth app" ->
{{"id":"104","journey_stage":"NA","lapse_type":"NA","drop_trigger":"NA","expectation":"NA","in_app_friction":"NA","investor_segment":"UNC","sentiment":"POS","relevance_flag":"Y","quote":"Nice smooth app","tag_reason":"generic app praise only"}}
"""


def resolve_tag_model(api_key: str) -> str:
    response = requests.get(
        f"{GROQ_BASE}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    available = {m["id"] for m in response.json().get("data", [])}
    for candidate in TAG_MODEL_PREFERRED:
        if candidate in available:
            return candidate
    for mid in sorted(available):
        if "120" in mid and "oss" in mid.lower():
            return mid
    raise RuntimeError(f"No gpt-oss-120b class model. Available sample: {sorted(available)[:6]}")


def load_filter_results() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not FILTER_RESULTS_PATH.is_file():
        return out
    for line in FILTER_RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("id"):
            out[rec["id"]] = rec
    return out


def load_tag_finished_ids() -> set[str]:
    return {key[0] for key in load_tag_finished_keys()}


def load_tag_finished_keys() -> set[tuple[str, str]]:
    if not TAG_RESULTS_PATH.is_file():
        return set()
    done: set[tuple[str, str]] = set()
    for line in TAG_RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("id") and rec.get("tag_status") != "failed":
            tagged_by = rec.get("tagged_by") or rec.get("model") or "default"
            done.add((rec["id"], str(tagged_by)))
    return done


def load_eligible_items() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    clean = {item_id(r): r for r in load_clean_rows()}
    filt = load_filter_results()
    items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for iid, frec in filt.items():
        if frec.get("relevant") not in ("yes", "unsure"):
            continue
        row = clean.get(iid)
        if row:
            items.append((iid, row, frec))
    return items


def relevance_code(filter_rec: dict[str, Any]) -> str:
    return "Y" if filter_rec.get("relevant") == "yes" else "U"


def build_user_payload(
    batch: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> str:
    items = []
    for iid, row, frec in batch:
        items.append(
            {
                "id": iid,
                "filter_relevant": frec.get("relevant"),
                "source": row.get("source"),
                "year": row.get("year"),
                "text": truncate_words(row.get("text") or "", TEXT_WORD_LIMIT),
                "context": truncate_words(row.get("context") or "", CONTEXT_WORD_LIMIT) or None,
            }
        )
    return json.dumps({"items": items}, ensure_ascii=False)


def _too_large_response(response: requests.Response) -> bool:
    if response.status_code == 413:
        return True
    if response.status_code != 400:
        return False
    body = response.text.lower()
    return any(
        k in body
        for k in (
            "context",
            "token",
            "too large",
            "length",
            "maximum",
        )
    )


def call_groq_tag_batch(
    cfg: Any,
    model: str,
    system_prompt: str,
    batch: list[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    stats: dict[str, Any],
) -> list[dict[str, Any]]:
    from pipeline.llm_client import chat_json_batch

    if not batch:
        return []

    user_content = (
        "Tag each item. Return JSON {\"results\":[...]} with every id.\n"
        + build_user_payload(batch)
    )
    try:
        return chat_json_batch(cfg, model, system_prompt, user_content, stats=stats, timeout=180)
    except (RuntimeError, requests.HTTPError, ValueError) as exc:
        if len(batch) > 1 and _should_split_batch(exc):
            mid = len(batch) // 2
            left = call_groq_tag_batch(cfg, model, system_prompt, batch[:mid], stats=stats)
            right = call_groq_tag_batch(cfg, model, system_prompt, batch[mid:], stats=stats)
            return left + right
        raise


def _should_split_batch(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if any(k in msg for k in ("context", "token", "too large", "length", "maximum")):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code in (413, 400):
            return True
    return False


def validate_tags(raw: dict[str, Any], codebook: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed = codebook.get("codes") or {}
    for field in _TAG_FIELDS:
        val = raw.get(field)
        field_codes = allowed.get(field) or {}
        if val is None or str(val) not in field_codes:
            errors.append(field)
    quote = raw.get("quote")
    if quote is not None and not isinstance(quote, str):
        errors.append("quote")
    reason = raw.get("tag_reason")
    if reason is not None and not isinstance(reason, str):
        errors.append("tag_reason")
    return errors


def expand_codes(raw: dict[str, Any], codebook: dict[str, Any]) -> dict[str, Any]:
    allowed = codebook.get("codes") or {}
    expanded: dict[str, str] = {}
    for field in _TAG_FIELDS:
        code = str(raw.get(field, "UNC"))
        expanded[field] = (allowed.get(field) or {}).get(code, code)
    return expanded


def closest_quote_span(text: str, quote: str) -> tuple[str | None, int]:
    if not text or not quote:
        return None, 0
    if quote in text:
        return quote, 100
    words = text.split()
    qwords = quote.split()
    if not qwords:
        return None, 0
    best_span = ""
    best_score = 0
    n = len(qwords)
    for width in range(max(1, n - 2), n + 4):
        if width > len(words):
            continue
        for i in range(0, len(words) - width + 1):
            span = " ".join(words[i : i + width])
            score = fuzz.ratio(quote.lower(), span.lower())
            if score > best_score:
                best_score = score
                best_span = span
    if len(text) <= 500:
        for i in range(len(text)):
            for j in range(i + min(5, len(text)), min(len(text), i + len(quote) + 40) + 1):
                span = text[i:j]
                if len(span) < 5:
                    continue
                score = fuzz.ratio(quote.lower(), span.lower())
                if score > best_score:
                    best_score = score
                    best_span = span
    if best_score >= QUOTE_MIN_SCORE:
        return best_span, best_score
    return None, best_score


def apply_quote_checks(text: str, quote: str | None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "quote_original": quote,
        "quote_repaired": False,
        "quote_hidden": False,
        "quote_match_score": None,
    }
    if not quote:
        meta["quote"] = None
        return meta
    if quote in text:
        meta["quote"] = quote
        meta["quote_match_score"] = 100
        return meta
    repaired, score = closest_quote_span(text, quote)
    meta["quote_match_score"] = score
    if repaired:
        meta["quote"] = repaired
        meta["quote_repaired"] = True
        return meta
    meta["quote"] = None
    meta["quote_hidden"] = True
    return meta


def reason_hallucination_flag(text: str, tag_reason: str) -> bool:
    tl = (text or "").lower()
    reason = (tag_reason or "").lower()
    for _label, terms in _REASON_HALLMARKS:
        if not any(t in reason for t in terms):
            continue
        if not any(t in tl for t in terms):
            return True
    return False


def process_batch_with_retries(
    cfg: Any,
    model: str,
    system_prompt: str,
    codebook: dict[str, Any],
    batch: list[tuple[str, dict[str, Any], dict[str, Any]]],
    stats: dict[str, Any],
    *,
    tagged_by: str,
) -> list[dict[str, Any]]:
    expected_ids = [iid for iid, _, _ in batch]
    collected: dict[str, dict[str, Any]] = {}

    def merge_results(ai_rows: list[dict[str, Any]]) -> None:
        for row in ai_rows:
            rid = str(row.get("id", ""))
            if rid:
                collected[rid] = row

    ai_rows = call_groq_tag_batch(cfg, model, system_prompt, batch, stats=stats)
    merge_results(ai_rows)
    missing = [entry for entry in batch if entry[0] not in collected]
    if missing:
        retry_rows = call_groq_tag_batch(cfg, model, system_prompt, missing, stats=stats)
        merge_results(retry_rows)

    outputs: list[dict[str, Any]] = []
    for iid, row, frec in batch:
        raw = collected.get(iid)
        if not raw:
            outputs.append(
                {
                    "id": iid,
                    "source": row.get("source"),
                    "tag_status": "failed",
                    "failure": "missing_from_model_reply",
                    "tagged_by": tagged_by,
                    "tagged_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            continue

        raw["relevance_flag"] = relevance_code(frec)
        errors = validate_tags(raw, codebook)
        if errors:
            retry = call_groq_tag_batch(
                cfg,
                model,
                system_prompt,
                [(iid, row, frec)],
                stats=stats,
            )
            if retry:
                raw = retry[0]
                raw["relevance_flag"] = relevance_code(frec)
            errors = validate_tags(raw, codebook)

        if errors:
            outputs.append(
                {
                    "id": iid,
                    "source": row.get("source"),
                    "tag_status": "failed",
                    "failure": f"invalid_codebook_fields:{','.join(errors)}",
                    "raw_model": raw,
                    "tagged_by": tagged_by,
                    "tagged_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            continue

        text = row.get("text") or ""
        quote_meta = apply_quote_checks(text, raw.get("quote"))
        record: dict[str, Any] = {
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
            "tag_reason": " ".join(str(raw.get("tag_reason", "")).split()[:10]),
            "reason_hallucination_flag": reason_hallucination_flag(
                text, str(raw.get("tag_reason", ""))
            ),
            "tagged_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "tagged_by": tagged_by,
        }
        outputs.append(record)
    return outputs


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
    print(f"Quota limit hit; sleeping {reset_s:.0f}s (--auto-resume)…")
    time.sleep(reset_s + 1)
    return True


def append_tag_results(records: list[dict[str, Any]]) -> None:
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    with _TAG_WRITE_LOCK:
        with TAG_RESULTS_PATH.open("a", encoding="utf-8") as handle:
            for rec in records:
                handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def save_tag_state(state: dict[str, Any]) -> None:
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    with _TAG_STATE_LOCK:
        TAG_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def stratified_sample(
    items: list[tuple[str, dict[str, Any], dict[str, Any]]],
    n: int,
    seed: int = 42,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    by_source: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for entry in items:
        by_source[entry[1].get("source") or "unknown"].append(entry)
    sources = sorted(by_source)
    rng = random.Random(seed)
    picked: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    per = max(1, n // max(1, len(sources)))
    for src in sources:
        pool = by_source[src][:]
        rng.shuffle(pool)
        picked.extend(pool[:per])
    if len(picked) < n:
        rest = [e for e in items if e not in picked]
        rng.shuffle(rest)
        picked.extend(rest[: n - len(picked)])
    rng.shuffle(picked)
    return picked[:n]


def run_tagging(
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
    finished = load_tag_finished_keys()
    pending = [e for e in items if (e[0], tagged_by) not in finished]
    stats: dict[str, Any] = {
        "run_label": run_label,
        "model": model,
        "tagged_by": tagged_by,
        "pending": len(pending),
        "already_done": len(finished),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "requests": 0,
        "processed_this_run": 0,
    }
    save_tag_state({**stats, "started_at": datetime.now(timezone.utc).isoformat()})

    idx = 0
    while idx < len(pending):
        batch = pending[idx : idx + BATCH_SIZE]
        try:
            records = process_batch_with_retries(
                cfg, model, system_prompt, codebook, batch, stats, tagged_by=tagged_by
            )
        except RuntimeError:
            limits = stats.get("last_rate_limits") or {}
            if daily_limit_reached(limits) and wait_for_reset(limits, auto_resume=auto_resume):
                continue
            raise
        append_tag_results(records)
        stats["processed_this_run"] += len(records)
        idx += BATCH_SIZE
        limits = stats.get("last_rate_limits") or {}
        save_tag_state({**stats, "last_rate_limits": limits, "updated_at": datetime.now(timezone.utc).isoformat()})
        if daily_limit_reached(limits):
            print("Quota limit reached (Groq headers).")
            if not wait_for_reset(limits, auto_resume=auto_resume):
                break

    stats["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_tag_state(stats)
    return stats


def print_pilot_report(
    pilot_items: list[tuple[str, dict[str, Any], dict[str, Any]]],
    stats: dict[str, Any],
    total_eligible: int,
) -> None:
    results: dict[str, dict[str, Any]] = {}
    if TAG_RESULTS_PATH.is_file():
        for line in TAG_RESULTS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("id"):
                    results[rec["id"]] = rec

    ok = [results[iid] for iid, _, _ in pilot_items if iid in results and results[iid].get("tag_status") == "ok"]

    print("\n=== TAG PILOT (50 items) ===")
    print(f"Model: {stats.get('model')}")
    print(f"Tagged OK: {len(ok)} / {len(pilot_items)}")

    lapse_counts = Counter(r["tags"]["lapse_type"] for r in ok)
    trigger_counts = Counter(r["tags"]["drop_trigger"] for r in ok)
    print("\nLapse type counts:", dict(lapse_counts))
    print("Drop trigger counts:", dict(trigger_counts))

    unclear_pct: dict[str, float] = {}
    for field in _TAG_FIELDS:
        if not ok:
            unclear_pct[field] = 0.0
            continue
        unc = sum(1 for r in ok if r["tags"].get(field) == "UNC")
        unclear_pct[field] = round(100.0 * unc / len(ok), 1)
    print("\n% UNCLEAR per field:")
    for field, pct in unclear_pct.items():
        print(f"  {field}: {pct}%")

    repaired = sum(1 for r in ok if r.get("quote_repaired"))
    hidden = sum(1 for r in ok if r.get("quote_hidden"))
    exact = sum(
        1
        for r in ok
        if r.get("quote") and not r.get("quote_repaired") and not r.get("quote_hidden")
    )
    hallu = sum(1 for r in ok if r.get("reason_hallucination_flag"))
    print("\nQuote checks:")
    print(f"  exact substring: {exact}")
    print(f"  rapidfuzz repaired (>={QUOTE_MIN_SCORE}): {repaired}")
    print(f"  hidden (score < {QUOTE_MIN_SCORE}): {hidden}")
    print(f"  reason_hallucination_flag: {hallu}")

    sample = ok[:]
    random.shuffle(sample)
    sample = sample[:8]
    print("\nSample table (8 items):")
    header = f"{'source':12} {'lapse':5} {'trigger':5} {'reason':28} quote"
    print(header)
    print("-" * len(header))
    for rec in sample:
        iid = rec["id"]
        row = next(r for i, r, _ in pilot_items if i == iid)
        q = (rec.get("quote") or "")[:50]
        reason = (rec.get("tag_reason") or "")[:28]
        print(
            f"{row.get('source','')[:12]:12} "
            f"{rec['tags'].get('lapse_type',''):5} "
            f"{rec['tags'].get('drop_trigger',''):5} "
            f"{reason:28} {q}"
        )

    tokens = int(stats.get("total_tokens") or 0)
    per_item = tokens / max(1, stats.get("processed_this_run", 1))
    daily_cap = DEFAULT_DAILY_TOKEN_LIMIT
    lim = stats.get("last_rate_limits") or {}
    if lim.get("limit_tokens"):
        try:
            daily_cap = int(float(lim["limit_tokens"]))
        except ValueError:
            pass
    est_days = (per_item * total_eligible) / daily_cap if daily_cap else 0
    print(f"\nTokens: {tokens} (~{per_item:.0f}/item)")
    print(f"Eligible yes/unsure total: {total_eligible}")
    print(f"Estimated days for full tag run ({daily_cap} token/day): ~{est_days:.1f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Groq codebook tagger (pilot only)")
    parser.add_argument("--pilot", action="store_true", help="Tag 50-item pilot sample")
    parser.add_argument("--auto-resume", action="store_true", help="Wait on Groq quota reset")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.pilot:
        parser.print_help()
        print("\nFull tagging is not enabled from CLI (pilot only).")
        return 1

    if not CLEAN_PATH.is_file():
        print(f"Missing {CLEAN_PATH}")
        return 1
    if not FILTER_RESULTS_PATH.is_file():
        print(f"Missing {FILTER_RESULTS_PATH}; run pipeline.filter first.")
        return 1

    try:
        from pipeline.llm_client import load_llm_config, resolve_tag_model

        cfg = load_llm_config()
        model = resolve_tag_model(cfg)
        codebook = load_codebook()
    except (RuntimeError, ValueError) as exc:
        print(exc)
        return 1

    system_prompt = build_system_prompt(codebook)
    print(f"Using LLM provider={cfg.provider} model={model}")

    eligible = load_eligible_items()
    if not eligible:
        print("No yes/unsure items in filter_results.jsonl")
        return 1

    pilot_items = stratified_sample(eligible, 50, seed=args.seed)
    stats = run_tagging(
        pilot_items,
        cfg=cfg,
        model=model,
        tagged_by=model,
        system_prompt=system_prompt,
        codebook=codebook,
        auto_resume=args.auto_resume,
        run_label="tag_pilot_50",
    )
    print_pilot_report(pilot_items, stats, total_eligible=len(eligible))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
