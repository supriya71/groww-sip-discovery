"""OpenAI-compatible LLM calls (Groq primary, xAI fallback for pilots)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv

GROQ_BASE = "https://api.groq.com/openai/v1"
XAI_BASE = "https://api.x.ai/v1"


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


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    base_url: str
    api_key: str


def load_llm_config() -> LlmConfig:
    from collect.paths import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env")
    groq = (os.environ.get("GROQ_API_KEY") or "").strip()
    if groq:
        return LlmConfig(provider="groq", base_url=GROQ_BASE, api_key=groq)
    xai = (os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY") or "").strip()
    if xai:
        return LlmConfig(provider="xai", base_url=XAI_BASE, api_key=xai)
    raise RuntimeError(
        "Set GROQ_API_KEY (console.groq.com) or GROK_API_KEY (xAI) in .env for LLM pilots."
    )


def list_models(cfg: LlmConfig) -> set[str]:
    response = requests.get(
        f"{cfg.base_url}/models",
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    return {m["id"] for m in response.json().get("data", [])}


def resolve_filter_model(cfg: LlmConfig) -> str:
    if cfg.provider == "xai":
        return "grok-3-mini"
    try:
        available = list_models(cfg)
    except Exception:
        available = set()
    for candidate in (
        "openai/gpt-oss-20b",
        "gpt-oss-20b",
        "openai/gpt-oss-120b",
    ):
        if candidate in available:
            return candidate
    for mid in sorted(available):
        if "oss" in mid.lower():
            return mid
    raise RuntimeError(f"No OSS filter model on Groq. Sample: {sorted(available)[:6]}")


def resolve_tag_model(cfg: LlmConfig) -> str:
    if cfg.provider == "xai":
        return "grok-3-mini"
    try:
        available = list_models(cfg)
    except Exception:
        available = set()
    for candidate in ("openai/gpt-oss-120b", "gpt-oss-120b", "openai/gpt-oss-20b"):
        if candidate in available:
            return candidate
    for mid in sorted(available):
        if "120" in mid and "oss" in mid.lower():
            return mid
    return resolve_filter_model(cfg)


def resolve_qwen_model(cfg: LlmConfig) -> str:
    try:
        available = list_models(cfg)
    except Exception:
        available = set()
    for candidate in (
        "qwen/qwen3-32b",
        "qwen-qwen3-32b",
        "qwen-2.5-32b-instruct",
        "qwen/qwen2.5-32b-instruct",
        "qwen/qwen3-30b-a3b",
    ):
        if candidate in available:
            return candidate
    for mid in sorted(available):
        if "qwen" in mid.lower():
            return mid
    raise RuntimeError(f"No Qwen model on Groq. Sample: {sorted(available)[:8]}")


def load_groq_config() -> LlmConfig:
    from collect.paths import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env")
    groq = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not groq:
        raise RuntimeError("GROQ_API_KEY required for dual-model tagging (console.groq.com).")
    return LlmConfig(provider="groq", base_url=GROQ_BASE, api_key=groq)


def chat_json_batch(
    cfg: LlmConfig,
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    stats: dict[str, Any],
    timeout: int = 180,
) -> list[dict[str, Any]]:
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if cfg.provider == "groq":
        payload["response_format"] = {"type": "json_object"}
    else:
        payload["response_format"] = {"type": "json_object"}

    max_attempts = 6
    for attempt in range(max_attempts):
        response = requests.post(
            f"{cfg.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        limits = read_rate_limits(response.headers)
        stats["last_rate_limits"] = limits
        stats["llm_provider"] = cfg.provider

        if response.status_code == 429:
            wait_s = (
                parse_reset_seconds(limits.get("retry_after"))
                or parse_reset_seconds(limits.get("reset_tokens"))
                or parse_reset_seconds(limits.get("reset_requests"))
                or min(60, 2**attempt)
            )
            stats["rate_limit_waits"] = stats.get("rate_limit_waits", 0) + 1
            time.sleep(wait_s)
            continue
        if response.status_code >= 500:
            time.sleep(min(30, 2**attempt))
            continue
        if response.status_code == 400 and cfg.provider == "xai":
            payload.pop("response_format", None)
            user_content_wrapped = (
                user_content + "\n\nReply with ONLY valid JSON, no markdown."
            )
            payload["messages"][1]["content"] = user_content_wrapped
            continue

        response.raise_for_status()
        body = response.json()
        usage = body.get("usage") or {}
        stats["prompt_tokens"] = stats.get("prompt_tokens", 0) + int(usage.get("prompt_tokens") or 0)
        stats["completion_tokens"] = stats.get("completion_tokens", 0) + int(
            usage.get("completion_tokens") or 0
        )
        stats["total_tokens"] = stats.get("total_tokens", 0) + int(usage.get("total_tokens") or 0)
        stats["requests"] = stats.get("requests", 0) + 1

        content = body["choices"][0]["message"]["content"]
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content.strip())
        results = parsed.get("results") or parsed.get("items") or []
        if not isinstance(results, list):
            raise ValueError("Model JSON missing results list")
        return results

    raise RuntimeError("LLM batch failed after retries")
