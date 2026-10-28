"""Load dashboard bundle from data/export/ only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT = REPO_ROOT / "data" / "export"
STREAMLIT_CLOUD_URL = "https://groww-sip-discovery.streamlit.app"

CHART_COLORS = [
    "#0072B2",
    "#E69F00",
    "#009E73",
    "#CC79A7",
    "#56B4E9",
    "#D55E00",
    "#F0E442",
    "#000000",
]


def export_path(name: str) -> Path:
    return EXPORT / name


@st.cache_data(show_spinner="Loading export bundle…")
def load_meta() -> dict[str, Any]:
    return json.loads(export_path("meta.json").read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def load_json(name: str) -> Any:
    return json.loads(export_path(name).read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def load_evidence() -> list[dict[str, Any]]:
    path = export_path("evidence.jsonl")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@st.cache_data(show_spinner=False)
def load_ask_corpus() -> list[dict[str, Any]]:
    path = export_path("ask_corpus.jsonl")
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@st.cache_resource(show_spinner="Loading retrieval index…")
def load_ask_embeddings() -> tuple[list[str], np.ndarray] | None:
    npz_path = export_path("ask_embeddings.npz")
    if not npz_path.is_file():
        return None
    data = np.load(npz_path, allow_pickle=True)
    ids = [str(x) for x in data["ids"].tolist()]
    emb = np.asarray(data["embeddings"], dtype=np.float32)
    return ids, emb


def bundle_ready() -> bool:
    return export_path("meta.json").is_file()


def active_counts(meta: dict[str, Any], *, exclude_reddit: bool) -> dict[str, Any]:
    counts = meta.get("counts") or {}
    if exclude_reddit and counts.get("exclude_reddit"):
        block = counts["exclude_reddit"]
        return {
            "tagged": block.get("tagged", meta.get("tagged_count", 0)),
            "relevant": block.get("relevant", meta.get("relevant_count", 0)),
            "by_source": block.get("by_source") or {},
        }
    return {
        "tagged": counts.get("tagged", meta.get("tagged_count", 0)),
        "relevant": counts.get("relevant", meta.get("relevant_count", 0)),
        "by_source": counts.get("by_source") or {},
    }


def source_mix_line(by_source: dict[str, dict[str, int]], *, relevant: bool) -> str:
    key = "relevant" if relevant else "tagged"
    parts = [
        f"{src} {vals.get(key, 0):,}"
        for src, vals in sorted(by_source.items())
        if vals.get(key)
    ]
    return " · ".join(parts) if parts else "—"


def render_data_banner(meta: dict[str, Any], counts: dict[str, Any]) -> None:
    as_of = meta.get("data_as_of") or meta.get("exported_at", "")[:10]
    defs = meta.get("definitions") or {}
    mix = source_mix_line(counts.get("by_source") or {}, relevant=True)
    staff = meta.get("excluded_staff_replies")
    staff_note = f" · {staff:,} staff replies excluded at export" if staff else ""
    st.info(
        f"**Data as of {as_of}** · **{counts['tagged']:,} tagged** · "
        f"**{counts['relevant']:,} relevant** · relevant by source: {mix}{staff_note}\n\n"
        f"**Tagged:** {defs.get('tagged', '')} **Relevant:** {defs.get('relevant', '')}"
    )


def filter_by_reddit_row(row: dict[str, Any], *, exclude_reddit: bool) -> dict[str, Any] | None:
    if not exclude_reddit:
        return row
    by_src = row.get("by_source") or {}
    posts = sum(v for src, v in by_src.items() if src != "reddit")
    if posts <= 0:
        return None
    out = dict(row)
    out["posts"] = posts
    return out


def init_view_filters() -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Sidebar filters; returns (meta, active_counts, exclude_reddit)."""
    meta = load_meta()
    exclude_reddit = st.sidebar.checkbox("Exclude curated Reddit", value=False)
    counts = active_counts(meta, exclude_reddit=exclude_reddit)
    st.sidebar.divider()
    st.sidebar.caption("Live dashboard")
    st.sidebar.link_button(
        "Open Streamlit Cloud app",
        STREAMLIT_CLOUD_URL,
        use_container_width=True,
    )
    return meta, counts, exclude_reddit
