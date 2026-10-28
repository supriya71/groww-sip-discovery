from __future__ import annotations

import re
from typing import Any


def matches_keyword_filter(text: str, filter_cfg: dict[str, Any]) -> bool:
    if not text or not text.strip():
        return False
    keywords = filter_cfg.get("keywords") or []
    if not keywords:
        return True
    case_insensitive = filter_cfg.get("case_insensitive", True)
    haystack = text if not case_insensitive else text.lower()
    mode = (filter_cfg.get("mode") or "any").lower()

    def keyword_hits(term: str) -> bool:
        needle = term if not case_insensitive else term.lower()
        if " " in needle.strip():
            return needle in haystack
        pattern = r"\b" + re.escape(needle) + r"\b"
        flags = re.IGNORECASE if case_insensitive else 0
        return re.search(pattern, haystack, flags) is not None

    hits = [keyword_hits(str(k)) for k in keywords]
    if mode == "all":
        return all(hits)
    return any(hits)
