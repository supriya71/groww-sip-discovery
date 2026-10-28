from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

from collect.config_loader import load_config
from collect.filter_config import get_keyword_filter
from collect.keywords import matches_keyword_filter
from collect.output import print_run_summary, write_corpus
from collect.paths import REPO_ROOT

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
# Default daily quota for YouTube Data API v3 (units)
DEFAULT_DAILY_QUOTA = 10_000


def _api_key() -> str | None:
    load_dotenv(REPO_ROOT / ".env")
    for name in ("YOUTUBE_API_KEY", "YOUTUBE_DATA_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _get(
    path: str,
    *,
    api_key: str,
    params: dict[str, Any],
    quota: dict[str, int],
    cost: int,
) -> dict[str, Any]:
    response = requests.get(
        f"{YOUTUBE_API_BASE}/{path}",
        params={**params, "key": api_key},
        timeout=60,
    )
    quota["used"] += cost
    response.raise_for_status()
    return response.json()


def _search_videos(
    api_key: str,
    query: str,
    *,
    max_results: int,
    region_code: str,
    relevance_language: str,
    quota: dict[str, int],
) -> list[str]:
    payload = _get(
        "search",
        api_key=api_key,
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(50, max_results),
            "regionCode": region_code,
            "relevanceLanguage": relevance_language,
        },
        quota=quota,
        cost=100,
    )
    return [item["id"]["videoId"] for item in payload.get("items") or [] if item.get("id")]


def _fetch_comments(
    api_key: str,
    video_id: str,
    *,
    max_pages: int,
    quota: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_token = None
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": 100,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _get(
            "commentThreads",
            api_key=api_key,
            params=params,
            quota=quota,
            cost=1,
        )
        for item in payload.get("items") or []:
            snippet = (item.get("snippet") or {}).get("topLevelComment") or {}
            inner = snippet.get("snippet") or {}
            rows.append(
                {
                    "source": "youtube",
                    "comment_id": snippet.get("id"),
                    "video_id": video_id,
                    "text": (inner.get("textDisplay") or "").strip(),
                    "at": inner.get("publishedAt"),
                    "author": inner.get("authorDisplayName"),
                    "like_count": inner.get("likeCount"),
                }
            )
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    return rows


def run_youtube() -> int:
    api_key = _api_key()
    if not api_key:
        print(
            "youtube: missing API key. Set YOUTUBE_API_KEY in .env (not printed)."
        )
        return 1

    config = load_config()
    cfg = config.get("youtube") or {}
    queries = cfg.get("search_queries") or []
    max_videos = int(cfg.get("max_videos_per_query") or 15)
    max_comment_pages = int(cfg.get("max_comment_pages_per_video") or 3)
    region_code = cfg.get("region_code") or "IN"
    relevance_language = cfg.get("relevance_language") or "en"
    filter_cfg = get_keyword_filter(config)
    quota = {"used": 0}

    if not queries:
        print("youtube: no search_queries in config.yaml")
        return 1

    all_rows: list[dict[str, Any]] = []
    seen_comment_ids: set[Any] = set()
    for query in queries:
        print(f"YouTube search: {query!r}…")
        try:
            video_ids = _search_videos(
                api_key,
                query,
                max_results=max_videos,
                region_code=region_code,
                relevance_language=relevance_language,
                quota=quota,
            )
        except Exception as exc:
            print(f"  Search failed: {exc}")
            continue
        for video_id in video_ids:
            try:
                comments = _fetch_comments(
                    api_key,
                    video_id,
                    max_pages=max_comment_pages,
                    quota=quota,
                )
            except Exception as exc:
                print(f"  Comments failed for {video_id}: {exc}")
                continue
            for row in comments:
                row["search_query"] = query
                cid = row.get("comment_id")
                if cid in seen_comment_ids:
                    continue
                seen_comment_ids.add(cid)
                all_rows.append(row)

    kept = [row for row in all_rows if matches_keyword_filter(row.get("text") or "", filter_cfg)]
    quota_used = quota["used"]
    manifest = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "search_queries": queries,
        "max_videos_per_query": max_videos,
        "max_comment_pages_per_video": max_comment_pages,
        "youtube_quota_units_used": quota_used,
        "youtube_quota_units_daily_default": DEFAULT_DAILY_QUOTA,
        "youtube_quota_fraction_of_daily": round(quota_used / DEFAULT_DAILY_QUOTA, 4),
        "keyword_filter": filter_cfg,
    }
    out = write_corpus("youtube", all_rows=all_rows, kept_rows=kept, manifest=manifest)
    manifest = __import__("json").loads((out / "manifest.json").read_text(encoding="utf-8"))
    print_run_summary("youtube", manifest)
    print(
        f"  YouTube API quota used: ~{quota_used} units "
        f"({manifest['youtube_quota_fraction_of_daily']:.1%} of {DEFAULT_DAILY_QUOTA} default daily)"
    )
    print(f"Wrote {out}")
    return 0
