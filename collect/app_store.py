from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

import requests

from collect.config_loader import load_config
from collect.filter_config import get_keyword_filter
from collect.keywords import matches_keyword_filter
from collect.output import print_run_summary, write_corpus

_APP_STORE_MAX_RSS_PAGES = 10


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(re.sub(r"\s+", " ", cleaned)).strip()


def _fetch_app_store_reviews(
    app_id: str,
    *,
    country: str,
    max_count: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, _APP_STORE_MAX_RSS_PAGES + 1):
        if len(rows) >= max_count:
            break
        url = (
            f"https://itunes.apple.com/{country}/rss/customerreviews/"
            f"page={page}/id={app_id}/sortby=mostrecent/json"
        )
        response = requests.get(url, timeout=60)
        if response.status_code != 200:
            break
        feed = response.json().get("feed") or {}
        entries = feed.get("entry") or []
        if not entries:
            break
        for entry in entries:
            rating = (entry.get("im:rating") or {}).get("label")
            if rating is None:
                continue
            content = entry.get("content") or {}
            body = content.get("label") if isinstance(content, dict) else str(content)
            updated = entry.get("updated", {}).get("label") if isinstance(entry.get("updated"), dict) else entry.get("updated")
            rows.append(
                {
                    "source": "app_store",
                    "review_id": (entry.get("id") or {}).get("label"),
                    "app_id": str(app_id),
                    "rating": int(rating) if str(rating).isdigit() else rating,
                    "title": (entry.get("title") or {}).get("label", ""),
                    "text": _strip_html(str(body)),
                    "at": updated,
                }
            )
            if len(rows) >= max_count:
                break
        if len(entries) < 2:
            break
    return rows[:max_count]


def run_app_store() -> int:
    config = load_config()
    cfg = config.get("app_store") or {}
    country = cfg.get("country", "in")
    max_per_app = int(cfg.get("max_reviews_per_app") or 500)
    apps = cfg.get("apps") or []
    filter_cfg = get_keyword_filter(config)

    if not apps:
        print("app_store: no apps under app_store.apps in config.yaml")
        return 1

    all_rows: list[dict[str, Any]] = []
    per_app: dict[str, int] = {}
    for app in apps:
        app_id = str(app.get("id") or app.get("app_id") or "")
        label = app.get("label") or app_id
        if not app_id:
            continue
        print(f"Fetching up to {max_per_app} App Store reviews for {label} ({app_id})…")
        try:
            batch = _fetch_app_store_reviews(app_id, country=country, max_count=max_per_app)
        except Exception as exc:
            print(f"  Failed for {label}: {exc}")
            per_app[label] = 0
            continue
        for row in batch:
            row["app_label"] = label
        per_app[label] = len(batch)
        all_rows.extend(batch)
        if not batch:
            print("  Warning: 0 reviews (Apple RSS caps ~500 per app).")

    kept = [row for row in all_rows if matches_keyword_filter(row.get("text") or "", filter_cfg)]
    manifest = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "country": country,
        "apps": apps,
        "pulled_by_app": per_app,
        "apple_rss_page_cap": _APP_STORE_MAX_RSS_PAGES,
        "keyword_filter": filter_cfg,
    }
    out = write_corpus("app_store", all_rows=all_rows, kept_rows=kept, manifest=manifest)
    manifest = __import__("json").loads((out / "manifest.json").read_text(encoding="utf-8"))
    print_run_summary("app_store", manifest)
    print(f"Wrote {out}")
    return 0
