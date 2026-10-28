from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import requests
from google_play_scraper import Sort, reviews

from collect.config_loader import load_config
from collect.filter_config import get_keyword_filter
from collect.keywords import matches_keyword_filter
from collect.output import print_run_summary, write_corpus


_SORT = {
    "newest": Sort.NEWEST,
    "rating": Sort.RATING,
    "most_relevant": Sort.MOST_RELEVANT,
}


def _fetch_app_reviews(
    app_id: str,
    *,
    lang: str,
    country: str,
    sort: Sort,
    max_count: int,
) -> list[dict[str, Any]]:
    pulled: list[dict[str, Any]] = []
    token = None
    while len(pulled) < max_count:
        batch = min(200, max_count - len(pulled))
        batch_rows, token = reviews(
            app_id,
            lang=lang,
            country=country,
            sort=sort,
            count=batch,
            continuation_token=token,
        )
        if not batch_rows:
            break
        pulled.extend(batch_rows)
        if token is None:
            break
    return pulled[:max_count]


def _normalize_review(row: dict[str, Any], app_id: str, app_label: str) -> dict[str, Any]:
    at = row.get("at")
    if at is not None and hasattr(at, "isoformat"):
        at = at.isoformat()
    return {
        "source": "play_store",
        "review_id": row.get("reviewId") or row.get("review_id"),
        "app_id": app_id,
        "app_label": app_label,
        "rating": row.get("score"),
        "text": (row.get("content") or "").strip(),
        "review_created_version": row.get("reviewCreatedVersion"),
        "thumbs_up": row.get("thumbsUpCount"),
        "at": at,
    }


def run_play_store() -> int:
    config = load_config()
    ps_cfg = config.get("play_store") or {}
    locale = ps_cfg.get("locale") or {}
    lang = locale.get("lang", "en")
    country = locale.get("country", "in")
    sort_name = (ps_cfg.get("sort") or "newest").lower()
    sort = _SORT.get(sort_name, Sort.NEWEST)
    max_per_app = int(ps_cfg.get("max_reviews_per_app") or 500)
    apps = ps_cfg.get("apps") or []
    filter_cfg = get_keyword_filter(config)

    if not apps:
        print("play_store: no apps configured under play_store.apps in config.yaml")
        return 1

    all_normalized: list[dict[str, Any]] = []
    per_app_pulled: dict[str, int] = {}
    for app in apps:
        app_id = app.get("id") or app.get("app_id")
        if not app_id:
            continue
        label = app.get("label") or app_id
        print(f"Fetching up to {max_per_app} reviews for {label} ({app_id})…")
        try:
            raw_rows = _fetch_app_reviews(
                app_id,
                lang=lang,
                country=country,
                sort=sort,
                max_count=max_per_app,
            )
        except Exception as exc:
            print(f"  Failed for {label}: {exc}")
            per_app_pulled[label] = 0
            continue
        per_app_pulled[label] = len(raw_rows)
        if not raw_rows:
            print("  Warning: 0 reviews returned (check app id or Play availability).")
        for row in raw_rows:
            all_normalized.append(_normalize_review(row, app_id, label))

    kept = [
        row
        for row in all_normalized
        if matches_keyword_filter(row.get("text") or "", filter_cfg)
    ]

    fetched_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "fetched_at": fetched_at,
        "locale": {"lang": lang, "country": country},
        "sort": sort_name,
        "apps": apps,
        "pulled_by_app": per_app_pulled,
        "keyword_filter": filter_cfg,
    }
    out = write_corpus("play_store", all_rows=all_normalized, kept_rows=kept, manifest=manifest)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"Reviews pulled: {manifest['reviews_pulled']} ({per_app_pulled})")
    print(f"Reviews kept (keyword filter): {manifest['reviews_kept']}")
    print_run_summary("play_store", manifest)
    print(f"Wrote {out / 'all_reviews.jsonl'}")
    print(f"Wrote {out / 'kept_reviews.jsonl'}")
    return 0
