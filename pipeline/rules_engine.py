"""Deterministic filter/tag fallback when LLM API is unavailable (pilot only)."""

from __future__ import annotations

import re
from typing import Any

from collect.config_loader import load_config
from collect.filter_config import get_keyword_filter
from collect.keywords import matches_keyword_filter

_STOCKS_ONLY = re.compile(
    r"\b(f&o|fno|intraday|option|future|nifty|banknifty|demat|ipo)\b",
    re.I,
)
_MF = re.compile(
    r"\b(sip|mutual fund|mf|elss|nav|lumpsum|mandate|autopay|redeem|folio)\b",
    re.I,
)
_PAUSE = re.compile(r"\b(pause|paused|stop|stopped|cancel|reduce|reduced)\b", re.I)
_MANDATE = re.compile(r"\bmandate|autopay|auto debit\b", re.I)
_MARKET = re.compile(r"\b(market crash|bear|fall|volatile|fear)\b", re.I)
_RETURNS = re.compile(r"\b(return|returns|xirr|profit|loss|underwhel|only \d+ rupee)\b", re.I)
_TRADING = re.compile(r"\b(stop-loss|stop loss|intraday|nifty|banknifty|share|trading|demat)\b", re.I)
_BEGINNER = re.compile(r"\b(beginner|first time|new investor|started)\b", re.I)
_GENERIC = re.compile(r"^(good|nice|best|smooth|easy|great)\b", re.I)
_FIRST = re.compile(r"\b(i|my|me|we|mujhe|mera|main)\b", re.I)


def filter_item(row: dict[str, Any]) -> dict[str, Any]:
    text = row.get("text") or ""
    tl = text.lower()
    sip_cfg = get_keyword_filter(load_config())
    has_mf = bool(_MF.search(text)) or matches_keyword_filter(text, sip_cfg)
    if _GENERIC.match(text.strip()) and not has_mf:
        return {"relevant": "no", "reason": "generic praise only"}
    if _STOCKS_ONLY.search(text) and not has_mf:
        return {"relevant": "no", "reason": "stocks or F and O only"}
    if not has_mf and not _BEGINNER.search(text):
        if any(w in tl for w in ("invest", "money", "payment", "withdraw", "deposit", "app")):
            return {"relevant": "unsure", "reason": "money or app issue unclear MF"}
        return {"relevant": "no", "reason": "no mutual fund angle"}
    if _BEGINNER.search(text) or "?" in text:
        return {"relevant": "unsure", "reason": "beginner or question"}
    if _FIRST.search(text) and (_PAUSE.search(text) or _MANDATE.search(text) or _RETURNS.search(text)):
        return {"relevant": "yes", "reason": "personal SIP or MF issue"}
    if has_mf:
        return {"relevant": "yes", "reason": "mentions SIP or mutual fund"}
    return {"relevant": "unsure", "reason": "weak MF signal"}


def _pick_quote(text: str) -> str:
    if not text:
        return ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if _MF.search(sent) or _PAUSE.search(sent):
            return sent.strip()[:200]
    words = text.split()
    return " ".join(words[: min(12, len(words))])


def tag_item(row: dict[str, Any], filter_rec: dict[str, Any]) -> dict[str, Any]:
    text = row.get("text") or ""
    tl = text.lower()
    rel = filter_rec.get("relevant", "unsure")
    rel_code = "Y" if rel == "yes" else "U"

    lapse = "UNC"
    if _PAUSE.search(text) and (_MF.search(text) or "sip" in tl):
        lapse = "PAU" if "pause" in tl else "STO"
    elif rel == "yes" and _MF.search(text) and not _PAUSE.search(text):
        lapse = "NON"

    trigger = "NA"
    if _MANDATE.search(text):
        trigger = "MD"
    elif _MARKET.search(text):
        trigger = "MF"
    elif _RETURNS.search(text) and not _TRADING.search(text):
        trigger = "UR"
    elif "cash" in text.lower() or "emi" in text.lower():
        trigger = "CF"

    journey = "UNC"
    if _BEGINNER.search(text) or "start" in text.lower():
        journey = "S1"
    elif _PAUSE.search(text):
        journey = "S3"

    friction = "NA"
    if "nav" in tl or "unit" in tl:
        friction = "NAV"
    elif _MANDATE.search(text) or "autopay" in tl:
        friction = "PM"
    elif "statement" in tl or "ledger" in tl:
        friction = "ST"
    elif re.search(r"\b(crash|hang|not open|slow|lag|freeze)\b", tl):
        friction = "KY"
    elif re.search(r"\b(confus|hard to use|difficult|ui)\b", tl):
        friction = "UI"

    segment = "YNG" if _BEGINNER.search(text) else "UNC"
    tl_neg = any(w in tl for w in ("bad", "worst", "fail", "cheat", "fraud", "useless", "bakwas", "pathetic"))
    sentiment = "NEG" if tl_neg else "NEU"
    if not tl_neg and any(w in tl for w in ("good", "best", "love", "smooth", "excellent")):
        sentiment = "POS"

    quote = _pick_quote(text)
    return {
        "journey_stage": journey,
        "lapse_type": lapse,
        "drop_trigger": trigger,
        "expectation": "UNC",
        "in_app_friction": friction,
        "investor_segment": segment,
        "sentiment": sentiment,
        "relevance_flag": rel_code,
        "quote": quote,
        "tag_reason": filter_rec.get("reason", "rules fallback tag")[:60],
    }
