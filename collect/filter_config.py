from __future__ import annotations

from typing import Any


def get_keyword_filter(config: dict[str, Any]) -> dict[str, Any]:
    shared = config.get("keyword_filter")
    if isinstance(shared, dict) and shared.get("keywords"):
        return shared
    for source_key in ("play_store", "app_store", "tradingqna", "youtube"):
        nested = (config.get(source_key) or {}).get("keyword_filter")
        if isinstance(nested, dict) and nested.get("keywords"):
            return nested
    return shared if isinstance(shared, dict) else {}
