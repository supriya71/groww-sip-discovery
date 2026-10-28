"""Reddit collection via Apify (pay-per-result actor).

  python -m collect.reddit_apify price
  python -m collect.reddit_apify test
  python -m collect.reddit_apify step1 --approved
  python -m collect.reddit_apify step2
  python -m collect.reddit_apify step3 --approved
  python -m collect.reddit_apify after-collect   # clean + tag queue + evidence (post step3)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collect.config_loader import load_config  # noqa: E402

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR = "trudax/reddit-scraper"
RESULT_EVENT_KEY = "apify-default-dataset-item"
START_EVENT_KEY = "apify-actor-start"
EVIDENCE_LOG = REPO_ROOT / "docs" / "evidence_log_notes.md"

RAW_REDDIT = REPO_ROOT / "data" / "raw" / "reddit"
BUDGET_PATH = RAW_REDDIT / "budget.json"
MANUAL_THREADS = REPO_ROOT / "data" / "manual" / "reddit_threads.txt"
TAG_QUEUE_PATH = REPO_ROOT / "data" / "processed" / "tag_queue.jsonl"

_USER_IN_TEXT = re.compile(r"\bu/[A-Za-z0-9_-]+\b", re.I)
_USER_IN_PATH = re.compile(r"(reddit\.com|redd\.it)/user/[A-Za-z0-9_-]+/?", re.I)
_HTML_TAG = re.compile(r"<[^>]+>")

_SHORTLIST_TITLE = re.compile(
    r"\b("
    r"stop|stopped|pause|paused|cancel|autopay|auto-pay|auto pay|mandate|failed|"
    r"first sip|negative|loss"
    r")\b",
    re.I,
)
_REDDIT_FOOTER = re.compile(
    r"submitted by\s+.*?(\[link\]|\[comments\]).*",
    re.I | re.S,
)
_MF_TITLE = re.compile(r"\b(sip|mutual fund|mutual funds|mf|elss)\b", re.I)


def _token() -> str:
    load_dotenv(REPO_ROOT / ".env")
    import os

    token = (os.environ.get("APIFY_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("APIFY_TOKEN missing in .env")
    return token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def actor_slug(actor_id: str) -> str:
    return actor_id.replace("/", "~")


def reddit_cfg() -> dict[str, Any]:
    return load_config().get("reddit_apify") or {}


def budget_limit() -> float:
    return float(reddit_cfg().get("budget_usd") or 3.50)


def load_spend() -> dict[str, Any]:
    if BUDGET_PATH.is_file():
        return json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    return {"spent_usd": 0.0, "runs": []}


def save_spend(data: dict[str, Any]) -> None:
    RAW_REDDIT.mkdir(parents=True, exist_ok=True)
    BUDGET_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fetch_actor_pricing(actor_id: str | None = None) -> dict[str, Any]:
    aid = actor_id or reddit_cfg().get("actor_id") or DEFAULT_ACTOR
    response = requests.get(
        f"{APIFY_BASE}/acts/{actor_slug(aid)}",
        headers=_headers(),
        timeout=60,
    )
    response.raise_for_status()
    infos = response.json()["data"].get("pricingInfos") or []
    for info in infos:
        if info.get("pricingModel") != "PAY_PER_EVENT":
            continue
        events = (info.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}
        start = float((events.get(START_EVENT_KEY) or {}).get("eventPriceUsd") or 0)
        result = float((events.get(RESULT_EVENT_KEY) or {}).get("eventPriceUsd") or 0)
        return {
            "actor_id": aid,
            "pricing_model": "PAY_PER_EVENT",
            "usd_per_result": result,
            "usd_per_run_start": start,
            "usd_per_1000_results": round(result * 1000, 4),
            "supports_thread_urls": True,
            "supports_comments": True,
            "notes": "Each dataset row (post/comment) bills as one result event.",
        }
    raise RuntimeError(f"No PAY_PER_EVENT pricing on actor {aid}")


def estimate_run_cost(max_results: int, pricing: dict[str, Any]) -> float:
    return pricing["usd_per_run_start"] + max_results * pricing["usd_per_result"]


def refuse_if_over_budget(max_results: int, pricing: dict[str, Any] | None = None) -> None:
    pricing = pricing or fetch_actor_pricing()
    spend = load_spend()
    projected = spend["spent_usd"] + estimate_run_cost(max_results, pricing)
    limit = budget_limit()
    if projected > limit + 1e-9:
        raise RuntimeError(
            f"Budget refused: projected ${projected:.4f} exceeds cap ${limit:.2f} "
            f"(already spent ${spend['spent_usd']:.4f}, this run up to {max_results} results)."
        )


def decode_text(value: str) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = _HTML_TAG.sub(" ", text)
    text = _USER_IN_TEXT.sub("", text)
    text = _USER_IN_PATH.sub("", text)
    text = _REDDIT_FOOTER.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_usernames_from_title(title: str) -> str:
    return decode_text(title)


def run_actor(actor_input: dict[str, Any], *, max_results: int, label: str) -> tuple[list[dict], float]:
    pricing = fetch_actor_pricing()
    refuse_if_over_budget(max_results, pricing)
    aid = reddit_cfg().get("actor_id") or DEFAULT_ACTOR
    slug = actor_slug(aid)

    start = requests.post(
        f"{APIFY_BASE}/acts/{slug}/runs",
        headers=_headers(),
        json=actor_input,
        params={"timeout": 300, "memory": 1024},
        timeout=60,
    )
    start.raise_for_status()
    run = start.json()["data"]
    run_id = run["id"]
    print(f"Apify run started: {run_id} ({label})")

    while True:
        status_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            headers=_headers(),
            timeout=60,
        )
        status_resp.raise_for_status()
        data = status_resp.json()["data"]
        status = data["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
        time.sleep(3)

    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run_id} ended with status {status}")

    usage_usd = float(data.get("usageTotalUsd") or 0)
    if usage_usd <= 0:
        items_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            headers=_headers(),
            params={"limit": 1},
            timeout=60,
        )
        count = int(items_resp.headers.get("x-apify-pagination-total") or 0)
        usage_usd = estimate_run_cost(count, pricing)

    dataset_id = data["defaultDatasetId"]
    items: list[dict] = []
    offset = 0
    while True:
        page = requests.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=_headers(),
            params={"offset": offset, "limit": 250, "clean": "true"},
            timeout=120,
        )
        page.raise_for_status()
        batch = page.json()
        if not batch:
            break
        items.extend(batch)
        offset += len(batch)
        if len(batch) < 250:
            break

    spend = load_spend()
    spend["spent_usd"] = round(spend["spent_usd"] + usage_usd, 6)
    spend.setdefault("runs", []).append(
        {
            "run_id": run_id,
            "label": label,
            "items": len(items),
            "usage_usd": usage_usd,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_spend(spend)
    return items, usage_usd


def normalize_post(raw: dict[str, Any]) -> dict[str, Any] | None:
    data_type = str(raw.get("dataType") or raw.get("type") or "").lower()
    if data_type and data_type not in ("post", "posts", ""):
        if "comment" in data_type:
            return None
    title = strip_usernames_from_title(str(raw.get("title") or raw.get("postTitle") or ""))
    body = decode_text(str(raw.get("body") or raw.get("selftext") or raw.get("text") or ""))
    text = f"{title}\n\n{body}".strip() if body else title
    if not text.strip():
        return None
    created = raw.get("createdAt") or raw.get("created_at") or raw.get("created")
    subreddit = raw.get("subreddit") or raw.get("communityName") or ""
    post_id = raw.get("id") or raw.get("postId") or raw.get("parsedId")
    url = raw.get("url") or raw.get("postUrl") or raw.get("link")
    return {
        "source": "reddit",
        "kind": "post",
        "post_id": post_id,
        "subreddit": subreddit,
        "text": text,
        "title": title,
        "at": created,
        "url": url,
    }


def normalize_comment(raw: dict[str, Any], post_context: str) -> dict[str, Any] | None:
    body = decode_text(str(raw.get("body") or raw.get("text") or raw.get("comment") or ""))
    if not body:
        return None
    created = raw.get("createdAt") or raw.get("created_at") or raw.get("created")
    comment_id = raw.get("id") or raw.get("commentId")
    post_id = raw.get("postId") or raw.get("post_id")
    return {
        "source": "reddit",
        "kind": "comment",
        "post_id": post_id,
        "comment_id": comment_id,
        "text": body,
        "context": post_context[:500],
        "at": created,
    }


def post_year(raw: dict[str, Any]) -> int | None:
    created = raw.get("createdAt") or raw.get("created_at") or raw.get("at")
    if not created:
        return None
    try:
        if isinstance(created, (int, float)):
            return datetime.fromtimestamp(created, tz=timezone.utc).year
        text = str(created)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).year
    except (ValueError, TypeError, OSError):
        return None


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def subreddit_search_url(subreddit: str, query: str) -> str:
    q = quote_plus(query)
    return f"https://www.reddit.com/r/{subreddit}/search/?q={q}&restrict_sr=1&sort=new"


def _proxy_config() -> dict[str, Any]:
    return {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}


def search_posts_input(
    query: str,
    subreddit: str,
    *,
    max_items: int,
    post_date_limit: str | None = None,
) -> dict[str, Any]:
    actor_input: dict[str, Any] = {
        "startUrls": [{"url": subreddit_search_url(subreddit, query)}],
        "maxItems": max_items,
        "maxPostCount": max_items,
        "skipComments": True,
        "proxy": _proxy_config(),
    }
    if post_date_limit:
        actor_input["postDateLimit"] = post_date_limit
    return actor_input


def thread_comments_input(url: str, *, max_items: int, max_comments: int) -> dict[str, Any]:
    return {
        "startUrls": [{"url": url}],
        "maxItems": max_items,
        "maxPostCount": 1,
        "maxComments": max_comments,
        "skipComments": False,
        "proxy": _proxy_config(),
    }


def cmd_price() -> int:
    pricing = fetch_actor_pricing()
    spend = load_spend()
    print(f"Actor: {pricing['actor_id']} ({pricing['pricing_model']})")
    print(f"Cost per result: ${pricing['usd_per_result']:.6f}")
    print(f"Cost per 1,000 results: ${pricing['usd_per_1000_results']:.4f}")
    print(f"Run start fee: ${pricing['usd_per_run_start']:.6f}")
    print(f"Budget cap: ${budget_limit():.2f} | spent so far: ${spend['spent_usd']:.4f}")
    return 0


def cmd_test() -> int:
    pricing = fetch_actor_pricing()
    max_items = int(reddit_cfg().get("test_max_items") or 30)
    print(f"Cost per 1,000 results: ${pricing['usd_per_1000_results']:.4f}")
    refuse_if_over_budget(max_items, pricing)
    actor_input = search_posts_input("paused SIP", "IndiaInvestments", max_items=max_items)
    raw_items, cost = run_actor(actor_input, max_results=max_items, label="test")
    normalized = [normalize_post(r) for r in raw_items]
    normalized = [n for n in normalized if n]
    out = RAW_REDDIT / "test_items.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in normalized),
        encoding="utf-8",
    )
    print(f"Items received: {len(raw_items)} raw -> {len(normalized)} posts | cost ${cost:.4f}")
    print("\n5 samples:")
    for row in normalized[:5]:
        print(f"  - {(row.get('text') or '')[:120]}")
    return 0


def cmd_step1(approved: bool) -> int:
    if not approved:
        print("Step 1 requires --approved (after you review test run cost/samples).")
        return 1
    cfg = reddit_cfg()
    target = int(cfg.get("posts_target") or 150)
    min_year = int(cfg.get("min_post_year") or 2021)
    post_date_limit = f"{min_year}-01-01"
    pricing = fetch_actor_pricing()
    refuse_if_over_budget(target + 10, pricing)

    all_posts: list[dict[str, Any]] = []
    per_query = max(5, target // max(1, len(cfg.get("search_queries") or [])))
    for sub in cfg.get("subreddits") or []:
        for query in cfg.get("search_queries") or []:
            remaining = target - len(all_posts)
            if remaining <= 0:
                break
            batch_cap = min(per_query, remaining)
            refuse_if_over_budget(batch_cap + 5, pricing)
            actor_input = search_posts_input(
                query,
                sub,
                max_items=batch_cap,
                post_date_limit=post_date_limit,
            )
            raw, cost = run_actor(actor_input, max_results=batch_cap, label=f"posts_{sub}_{query[:20]}")
            for item in raw:
                norm = normalize_post(item)
                if not norm:
                    continue
                yr = post_year(item) or post_year(norm)
                if yr is not None and yr < min_year:
                    continue
                all_posts.append(norm)
            print(f"  {sub} | {query[:30]} -> {len(raw)} raw, cost ${cost:.4f}")
        if len(all_posts) >= target:
            break

    all_posts = all_posts[:target]
    path = RAW_REDDIT / "posts_step1.jsonl"
    path.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in all_posts),
        encoding="utf-8",
    )
    manifest = {
        "step": 1,
        "posts": len(all_posts),
        "min_year": min_year,
        "spent_total": load_spend()["spent_usd"],
    }
    (RAW_REDDIT / "manifest_step1.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Step 1 saved {len(all_posts)} posts to {path}")
    return 0


def cmd_step2() -> int:
    path = RAW_REDDIT / "posts_step1.jsonl"
    if not path.is_file():
        print("Run step1 first.")
        return 1
    posts = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    shortlist: list[dict[str, Any]] = []
    print("\nShortlisted titles (step 2 — free filter):")
    for post in posts:
        title = post.get("title") or post.get("text", "").split("\n", 1)[0]
        tl = title.lower()
        if not _SHORTLIST_TITLE.search(tl):
            continue
        if not _MF_TITLE.search(tl) and not _MF_TITLE.search(post.get("text", "").lower()):
            continue
        shortlist.append(post)
        print(f"  - {title[:100]}")

    (RAW_REDDIT / "shortlist.json").write_text(
        json.dumps(shortlist, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nShortlist count: {len(shortlist)}")
    return 0


def _manual_urls() -> list[str]:
    if not MANUAL_THREADS.is_file():
        return []
    urls = []
    for line in MANUAL_THREADS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def cmd_step3(approved: bool) -> int:
    if not approved:
        print("Step 3 requires --approved.")
        return 1
    cfg = reddit_cfg()
    comments_n = int(cfg.get("comments_per_post") or 15)
    pricing = fetch_actor_pricing()
    spend = load_spend()

    posts: list[dict[str, Any]] = []
    if (RAW_REDDIT / "shortlist.json").is_file():
        posts = json.loads((RAW_REDDIT / "shortlist.json").read_text(encoding="utf-8"))
    post_by_url = {p.get("url"): p for p in posts if p.get("url")}

    urls = _manual_urls()
    for p in posts:
        if p.get("url") and p["url"] not in urls:
            urls.append(p["url"])

    if not urls:
        print("No URLs to fetch (shortlist + manual file empty).")
        return 1

    max_results = len(urls) * (comments_n + 2)
    refuse_if_over_budget(max_results, pricing)

    all_comments: list[dict[str, Any]] = []
    for url in urls:
        batch_cap = comments_n + 5
        refuse_if_over_budget(batch_cap, pricing)
        actor_input = thread_comments_input(url, max_items=batch_cap, max_comments=comments_n)
        raw, cost = run_actor(actor_input, max_results=batch_cap, label=f"comments")
        post_ctx = ""
        if url in post_by_url:
            post_ctx = post_by_url[url].get("title") or post_by_url[url].get("text", "")[:200]
        for item in raw:
            if item.get("title") and not item.get("body") and not item.get("text"):
                continue
            norm = normalize_comment(item, post_ctx)
            if norm:
                all_comments.append(norm)
        print(f"  {url[:70]}… -> {len(raw)} raw, ${cost:.4f}")

    write_jsonl(RAW_REDDIT / "comments_step3.jsonl", all_comments)
    write_jsonl(RAW_REDDIT / "all_items.jsonl", posts + all_comments)
    manifest = {
        "step": 3,
        "comments": len(all_comments),
        "urls": len(urls),
        "spent_total": load_spend()["spent_usd"],
    }
    (RAW_REDDIT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Step 3: {len(all_comments)} comments | total spend ${load_spend()['spent_usd']:.4f}")
    return 0


def cmd_after_collect() -> int:
    from pipeline.clean import run_clean

    run_clean()
    build_tag_queue()
    append_evidence_reddit()
    print("Clean complete; tag_queue.jsonl written (reddit first, split taggers).")
    return 0


def build_tag_queue() -> None:
    clean_path = REPO_ROOT / "data" / "processed" / "clean.jsonl"
    if not clean_path.is_file():
        return
    reddit_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []
    for line in clean_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("source") == "reddit":
            reddit_rows.append(row)
        else:
            other_rows.append(row)

    def tagger_for(row: dict[str, Any]) -> str:
        key = row.get("comment_id") or row.get("post_id") or row.get("text", "")[:80]
        digest = int(hashlib.sha256(str(key).encode()).hexdigest(), 16)
        return "openai/gpt-oss-120b" if digest % 2 == 0 else "qwen/qwen3-32b"

    TAG_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TAG_QUEUE_PATH.open("w", encoding="utf-8") as handle:
        for row in reddit_rows:
            handle.write(
                json.dumps(
                    {**row, "queue_order": "reddit_first", "assigned_tagger": tagger_for(row)},
                    ensure_ascii=False,
                )
                + "\n"
            )
        for row in other_rows:
            handle.write(
                json.dumps({**row, "queue_order": "other"}, ensure_ascii=False) + "\n"
            )


def append_evidence_reddit() -> None:
    spend = load_spend()
    pricing = fetch_actor_pricing()
    lines = [
        "",
        "## Reddit ingest (Apify `trudax/reddit-scraper`)",
        "",
        f"- Tool: Apify actor `{pricing['actor_id']}` (pay-per-result dataset items).",
        f"- Price: **${pricing['usd_per_1000_results']:.4f} / 1,000 results** (${pricing['usd_per_result']:.6f}/item).",
        f"- Budget cap: ${budget_limit():.2f} | recorded spend: ${spend['spent_usd']:.4f}.",
        "- Method: Step1 subreddit search (posts only, 2021+); step2 title shortlist (free); step3 thread URLs + 15 comments/post.",
        "- Limitations: Pay-per-event billing; search time filters depend on actor; usernames stripped; segment fields still mostly UNC in public text.",
        "- Tagging: `data/processed/tag_queue.jsonl` lists **reddit first**, split across two taggers (no duplicate assignment).",
        "",
    ]
    existing = EVIDENCE_LOG.read_text(encoding="utf-8") if EVIDENCE_LOG.is_file() else "# Evidence log\n"
    EVIDENCE_LOG.write_text(existing + "\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reddit collector (Apify)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("price", help="Show $/1k results and budget")
    sub.add_parser("test", help="Test run max 30 posts")
    p1 = sub.add_parser("step1", help="Fetch ~150 posts (needs --approved)")
    p1.add_argument("--approved", action="store_true")
    sub.add_parser("step2", help="Shortlist titles (free)")
    p3 = sub.add_parser("step3", help="Fetch comments (needs --approved)")
    p3.add_argument("--approved", action="store_true")
    sub.add_parser("after-collect", help="Run clean + tag queue + evidence")
    args = parser.parse_args()

    try:
        if args.cmd == "price":
            return cmd_price()
        if args.cmd == "test":
            return cmd_test()
        if args.cmd == "step1":
            return cmd_step1(args.approved)
        if args.cmd == "step2":
            return cmd_step2()
        if args.cmd == "step3":
            return cmd_step3(args.approved)
        if args.cmd == "after-collect":
            return cmd_after_collect()
    except RuntimeError as exc:
        print(exc)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
