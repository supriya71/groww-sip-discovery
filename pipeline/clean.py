"""Merge raw collector JSONL, dedupe, length-filter, enrich; write processed outputs.

Run from repo root: python -m pipeline.clean
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collect.config_loader import load_config  # noqa: E402
from collect.filter_config import get_keyword_filter  # noqa: E402
from collect.keywords import matches_keyword_filter  # noqa: E402
from collect.output import parse_date  # noqa: E402

RAW_ROOT = REPO_ROOT / "data" / "raw"
PROCESSED_ROOT = REPO_ROOT / "data" / "processed"
EVIDENCE_LOG = REPO_ROOT / "docs" / "evidence_log_notes.md"

MIN_WORDS = 6
NEAR_DUPE_THRESHOLD = 92
PRIMARY_JSONL = ("all_items.jsonl", "all_reviews.jsonl")

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_LATIN = re.compile(r"[A-Za-z]")
_WS = re.compile(r"\s+")

_ROMAN_HINDI_MARKERS = (
    "hai",
    "nahi",
    "nahin",
    "kya",
    "mujhe",
    "bahut",
    "accha",
    "achha",
    "bhai",
    "mera",
    "meri",
    "aap",
    "yeh",
    "ye",
    "kar",
    "karo",
    "paisa",
    "paise",
)


def normalize_text(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


def word_count(text: str) -> int:
    return len(_WS.split((text or "").strip())) if (text or "").strip() else 0


def detect_language(text: str) -> str:
    if not (text or "").strip():
        return "other"
    dev = len(_DEVANAGARI.findall(text))
    lat = len(_LATIN.findall(text))
    if dev > 0 and lat > 0:
        return "hinglish"
    if dev > 0:
        return "hi"
    if lat > 0:
        lower = f" {text.lower()} "
        if any(f" {marker} " in lower for marker in _ROMAN_HINDI_MARKERS):
            return "hinglish"
        return "en"
    return "other"


def extract_year(row: dict[str, Any]) -> int | None:
    parsed = parse_date(row.get("at"))
    if parsed:
        return parsed.year
    return None


def extract_context(row: dict[str, Any]) -> str | None:
    source = row.get("source") or ""
    if source == "tradingqna":
        title = (row.get("topic_title") or "").strip()
        if title:
            return title
        text = row.get("text") or ""
        if "\n\n" in text:
            return text.split("\n\n", 1)[0].strip() or None
        return None
    if source == "youtube":
        title = (row.get("video_title") or row.get("videoTitle") or "").strip()
        return title or None
    if source == "reddit":
        if row.get("kind") == "comment":
            return (row.get("context") or "").strip() or None
        title = (row.get("title") or "").strip()
        return title or None
    return None


def iter_raw_jsonl_files() -> list[Path]:
    paths: list[Path] = []
    if not RAW_ROOT.is_dir():
        return paths
    for subdir in sorted(RAW_ROOT.iterdir()):
        if not subdir.is_dir() or subdir.name in ("html",):
            continue
        chosen = None
        for name in PRIMARY_JSONL:
            candidate = subdir / name
            if candidate.is_file():
                chosen = candidate
                break
        if chosen:
            paths.append(chosen)
    paths.sort(key=lambda p: (0 if p.parent.name == "reddit" else 1, str(p)))
    return paths


def load_merged_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in iter_raw_jsonl_files():
        source_hint = path.parent.name
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("source"):
                row["source"] = source_hint
            text = (row.get("text") or "").strip()
            if not text:
                continue
            row["text"] = text
            row["_raw_file"] = str(path.relative_to(REPO_ROOT))
            rows.append(row)
    return rows


def _block_key(norm: str) -> tuple[int, str]:
    words = norm.split()
    prefix = " ".join(words[:4]) if words else ""
    return (len(norm) // 25, prefix[:48])


class NearDupeIndex:
    def __init__(self, threshold: int = NEAR_DUPE_THRESHOLD) -> None:
        self.threshold = threshold
        self._blocks: dict[tuple[int, str], list[str]] = defaultdict(list)

    def is_duplicate(self, norm: str) -> bool:
        key = _block_key(norm)
        bucket, prefix = key
        for b in (bucket - 1, bucket, bucket + 1):
            for rep in self._blocks.get((b, prefix), []):
                if fuzz.ratio(norm, rep) >= self.threshold:
                    return True
        return False

    def add(self, norm: str) -> None:
        self._blocks[_block_key(norm)].append(norm)


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    out["year"] = extract_year(row)
    out["language"] = detect_language(row.get("text") or "")
    out["context"] = extract_context(row)
    return out


def run_clean() -> dict[str, Any]:
    config = load_config()
    sip_filter = get_keyword_filter(config)

    raw_rows = load_merged_rows()
    raw_by_source = Counter(r.get("source") or "unknown" for r in raw_rows)

    raw_rows.sort(key=lambda r: (-word_count(r.get("text") or ""), r.get("at") or ""))

    exact_seen: set[str] = set()
    near_index = NearDupeIndex()
    after_dedupe: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    dedupe_dropped_by_source: Counter[str] = Counter()

    for row in raw_rows:
        source = row.get("source") or "unknown"
        norm = normalize_text(row.get("text") or "")
        if not norm:
            continue
        if norm in exact_seen:
            dropped.append(
                {
                    **enrich_row(row),
                    "drop_reason": "exact_duplicate",
                    "detail": "normalized text already kept",
                }
            )
            dedupe_dropped_by_source[source] += 1
            continue
        if near_index.is_duplicate(norm):
            dropped.append(
                {
                    **enrich_row(row),
                    "drop_reason": "near_duplicate",
                    "detail": f"rapidfuzz ratio>={NEAR_DUPE_THRESHOLD} vs kept text",
                }
            )
            dedupe_dropped_by_source[source] += 1
            continue
        exact_seen.add(norm)
        near_index.add(norm)
        after_dedupe.append(row)

    dedupe_by_source = Counter(r.get("source") or "unknown" for r in after_dedupe)

    clean_rows: list[dict[str, Any]] = []
    length_dropped_by_source: Counter[str] = Counter()

    for row in after_dedupe:
        source = row.get("source") or "unknown"
        text = row.get("text") or ""
        wc = word_count(text)
        if wc < MIN_WORDS and not matches_keyword_filter(text, sip_filter):
            dropped.append(
                {
                    **enrich_row(row),
                    "drop_reason": "too_short",
                    "detail": f"word_count={wc} (<{MIN_WORDS}) and no SIP keyword",
                    "word_count": wc,
                }
            )
            length_dropped_by_source[source] += 1
            continue
        clean_rows.append(enrich_row(row))

    clean_by_source = Counter(r.get("source") or "unknown" for r in clean_rows)

    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    clean_path = PROCESSED_ROOT / "clean.jsonl"
    dropped_path = PROCESSED_ROOT / "dropped.jsonl"
    clean_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in clean_rows),
        encoding="utf-8",
    )
    dropped_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in dropped),
        encoding="utf-8",
    )

    funnel: dict[str, dict[str, int]] = {}
    all_sources = sorted(set(raw_by_source) | set(dedupe_by_source) | set(clean_by_source))
    for src in all_sources:
        funnel[src] = {
            "raw": raw_by_source.get(src, 0),
            "after_dedupe": dedupe_by_source.get(src, 0),
            "after_length_filter": clean_by_source.get(src, 0),
        }

    year_counts = Counter(r.get("year") for r in clean_rows if r.get("year") is not None)
    lang_counts = Counter(r.get("language") for r in clean_rows)

    short_dropped = [d for d in dropped if d.get("drop_reason") == "too_short"]
    sample_short = random.sample(short_dropped, min(5, len(short_dropped)))

    stats = {
        "merged_files": [str(p.relative_to(REPO_ROOT)) for p in iter_raw_jsonl_files()],
        "min_words_rule": MIN_WORDS,
        "sip_keyword_exception": True,
        "near_dupe_threshold": NEAR_DUPE_THRESHOLD,
        "totals": {
            "raw": len(raw_rows),
            "after_dedupe": len(after_dedupe),
            "after_length_filter": len(clean_rows),
            "dropped_total": len(dropped),
        },
        "funnel_by_source": funnel,
        "year_counts": dict(sorted(year_counts.items())),
        "language_counts": dict(lang_counts),
    }

    _print_report(stats, sample_short)
    _append_evidence_log(stats)
    return stats


def _print_report(stats: dict[str, Any], sample_short: list[dict[str, Any]]) -> None:
    print("=== Clean pipeline funnel (per source) ===")
    print(f"Rule: drop texts with <{MIN_WORDS} words unless SIP keyword from config.yaml matches.")
    print(f"Near-duplicate threshold: rapidfuzz ratio >= {NEAR_DUPE_THRESHOLD}")
    print(f"Merged files: {', '.join(stats['merged_files']) or '(none)'}")
    print()
    for src, stages in stats["funnel_by_source"].items():
        print(
            f"  {src}: raw={stages['raw']} -> dedupe={stages['after_dedupe']} "
            f"-> length={stages['after_length_filter']}"
        )
    totals = stats["totals"]
    print()
    print(
        f"TOTAL: raw={totals['raw']} -> dedupe={totals['after_dedupe']} "
        f"-> clean={totals['after_length_filter']} (dropped={totals['dropped_total']})"
    )
    print()
    print("=== Clean set: counts by year ===")
    for year, count in stats["year_counts"].items():
        print(f"  {year}: {count}")
    print()
    print("=== Clean set: counts by language ===")
    for lang, count in stats["language_counts"].items():
        print(f"  {lang}: {count}")
    print()
    print("=== Sample dropped short items (check for false losses) ===")
    for item in sample_short:
        text = (item.get("text") or "")[:120]
        print(f"  [{item.get('source')}] wc={item.get('word_count')} text={text!r}")


def _append_evidence_log(stats: dict[str, Any]) -> None:
    EVIDENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "",
        f"## {ts} — `pipeline/clean.py` (merge, dedupe, length filter)",
        "",
        "### Short-text rule",
        f"- Drop items with **fewer than {MIN_WORDS} words** unless the text matches at least one "
        "entry in `keyword_filter.keywords` in `config.yaml` (same matcher as collectors).",
        "- SIP keyword exception keeps terse but on-topic lines (e.g. “SIP not working”).",
        "",
        "### Dedupe",
        f"- Exact: normalized lowercase collapsed whitespace.",
        f"- Near: rapidfuzz `ratio` ≥ {NEAR_DUPE_THRESHOLD} with length/prefix blocking.",
        "",
        "### Funnel (raw → after dedupe → after length filter)",
        "",
        "| Source | Raw | After dedupe | After length filter |",
        "| --- | ---: | ---: | ---: |",
    ]
    for src, stages in stats["funnel_by_source"].items():
        lines.append(
            f"| {src} | {stages['raw']} | {stages['after_dedupe']} | {stages['after_length_filter']} |"
        )
    t = stats["totals"]
    lines.append(f"| **TOTAL** | **{t['raw']}** | **{t['after_dedupe']}** | **{t['after_length_filter']}** |")
    lines.append("")
    lines.append("### Outputs")
    lines.append("- `data/processed/clean.jsonl`")
    lines.append("- `data/processed/dropped.jsonl` (includes `drop_reason`)")
    lines.append("")

    if EVIDENCE_LOG.is_file():
        existing = EVIDENCE_LOG.read_text(encoding="utf-8")
    else:
        existing = "# Evidence log — discovery engine decisions\n"

    EVIDENCE_LOG.write_text(existing + "\n".join(lines), encoding="utf-8")
    print(f"\nAppended funnel to {EVIDENCE_LOG.relative_to(REPO_ROOT)}")


def main() -> int:
    if not iter_raw_jsonl_files():
        print("No collector JSONL found under data/raw/<source>/all_items.jsonl or all_reviews.jsonl")
        return 1
    run_clean()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
