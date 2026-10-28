from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from collect.config_loader import load_config
from collect.filter_config import get_keyword_filter
from collect.keywords import matches_keyword_filter
from collect.output import print_run_summary, write_corpus


def _fetch_search_page(
    base_url: str,
    query: str,
    page: int,
    user_agent: str,
) -> dict[str, Any]:
    response = requests.get(
        f"{base_url.rstrip('/')}/search.json",
        params={"q": query, "page": page},
        headers={"User-Agent": user_agent},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _normalize_posts(
    payload: dict[str, Any],
    *,
    query: str,
    topics_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for post in payload.get("posts") or []:
        topic_id = post.get("topic_id")
        topic = topics_by_id.get(topic_id) or {}
        title = topic.get("title") or ""
        blurb = (post.get("blurb") or "").strip()
        text = f"{title}\n\n{blurb}".strip()
        rows.append(
            {
                "source": "tradingqna",
                "post_id": post.get("id"),
                "topic_id": topic_id,
                "topic_title": title,
                "username": post.get("username"),
                "text": text,
                "at": post.get("created_at"),
                "search_query": query,
                "url": f"https://www.tradingqna.com/t/{topic_id}" if topic_id else None,
            }
        )
    return rows


def run_tradingqna() -> int:
    config = load_config()
    cfg = config.get("tradingqna") or {}
    base_url = cfg.get("base_url") or "https://www.tradingqna.com"
    queries = cfg.get("search_queries") or []
    max_pages = int(cfg.get("max_pages_per_query") or 5)
    user_agent = cfg.get("user_agent") or "SIPDiscoveryBot/1.0"
    filter_cfg = get_keyword_filter(config)

    if not queries:
        print("tradingqna: no search_queries in config.yaml")
        return 1

    all_rows: list[dict[str, Any]] = []
    seen_post_ids: set[Any] = set()
    for query in queries:
        print(f"Searching TradingQ&A: {query!r} (up to {max_pages} pages)…")
        for page in range(1, max_pages + 1):
            try:
                payload = _fetch_search_page(base_url, query, page, user_agent)
            except Exception as exc:
                print(f"  Page {page} failed: {exc}")
                break
            topics = payload.get("topics") or []
            topics_by_id = {t["id"]: t for t in topics if "id" in t}
            batch = _normalize_posts(payload, query=query, topics_by_id=topics_by_id)
            if not batch:
                break
            for row in batch:
                pid = row.get("post_id")
                if pid in seen_post_ids:
                    continue
                seen_post_ids.add(pid)
                all_rows.append(row)

    kept = [row for row in all_rows if matches_keyword_filter(row.get("text") or "", filter_cfg)]
    manifest = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "search_queries": queries,
        "max_pages_per_query": max_pages,
        "unique_posts_pulled": len(all_rows),
        "keyword_filter": filter_cfg,
    }
    out = write_corpus("tradingqna", all_rows=all_rows, kept_rows=kept, manifest=manifest)
    manifest = __import__("json").loads((out / "manifest.json").read_text(encoding="utf-8"))
    print_run_summary("tradingqna", manifest)
    print(f"Wrote {out}")
    return 0
