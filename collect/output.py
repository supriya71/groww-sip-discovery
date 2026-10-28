from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from collect.paths import output_dir


def parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def date_range(rows: list[dict[str, Any]], field: str = "at") -> dict[str, str | None]:
    dates = [parse_date(row.get(field)) for row in rows]
    dates = [d for d in dates if d is not None]
    if not dates:
        return {"min": None, "max": None}
    return {
        "min": min(dates).date().isoformat(),
        "max": max(dates).date().isoformat(),
    }


def write_corpus(
    source: str,
    *,
    all_rows: list[dict[str, Any]],
    kept_rows: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> Path:
    out = output_dir(source)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        **manifest,
        "source": source,
        "items_pulled": len(all_rows),
        "items_kept": len(kept_rows),
        "date_range_pulled": date_range(all_rows),
        "date_range_kept": date_range(kept_rows),
    }
    (out / "all_items.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in all_rows),
        encoding="utf-8",
    )
    (out / "kept_items.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in kept_rows),
        encoding="utf-8",
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # Back-compat for play_store review-oriented filenames
    if source == "play_store":
        (out / "all_reviews.jsonl").write_text(
            (out / "all_items.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (out / "kept_reviews.jsonl").write_text(
            (out / "kept_items.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        manifest["reviews_pulled"] = len(all_rows)
        manifest["reviews_kept"] = len(kept_rows)
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return out


def print_run_summary(source: str, manifest: dict[str, Any]) -> None:
    pulled = manifest.get("items_pulled", manifest.get("reviews_pulled"))
    kept = manifest.get("items_kept", manifest.get("reviews_kept"))
    print(f"{source}: pulled={pulled} kept={kept}")
    print(f"  date range (pulled): {manifest.get('date_range_pulled')}")
    print(f"  date range (kept):   {manifest.get('date_range_kept')}")
