"""Secrets: Streamlit Cloud st.secrets first, then local .env (dev only)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def groq_api_key() -> str:
    """GROQ_API_KEY for optional LLM features (empty if unset)."""
    try:
        import streamlit as st

        val = st.secrets.get("GROQ_API_KEY", "")
        if val and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except Exception:
        pass
    return (os.environ.get("GROQ_API_KEY") or "").strip()
