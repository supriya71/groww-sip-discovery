"""SIP 12M retention discovery dashboard — reads only data/export/.

  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data_bundle import (
    CHART_COLORS,
    bundle_ready,
    filter_by_reddit_row,
    init_view_filters,
    load_ask_corpus,
    load_ask_embeddings,
    load_evidence,
    load_json,
    load_meta,
    render_data_banner,
    source_mix_line,
)
from app.secrets import groq_api_key  # noqa: F401 — Cloud: set in st.secrets

USAGE_PATH = REPO_ROOT / "data" / "export" / ".ask_usage.json"
SESSION_QUESTION_CAP = 10
DAILY_QUESTION_CAP = 30

st.set_page_config(
    page_title="SIP 12M Discovery",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _require_bundle():
    if not bundle_ready():
        st.error(
            "Export bundle missing. Run **`python -m pipeline.export`** from the repo root, "
            "then refresh this app."
        )
        st.stop()
    return init_view_filters()


def page_overview() -> None:
    meta, counts, _exclude = _require_bundle()
    overview = load_json("overview.json")
    mix_t = overview.get("source_mix_tagged") or source_mix_line(counts["by_source"], relevant=False)
    mix_r = source_mix_line(counts["by_source"], relevant=True)
    st.title(f"Overview: corpus by source — tagged {mix_t}; relevant {mix_r}")
    render_data_banner(meta, counts)

    with st.expander("How the discovery engine works", expanded=False):
        steps = overview.get("pipeline_diagram") or []
        cols = st.columns(len(steps))
        for i, step in enumerate(steps):
            with cols[i]:
                st.markdown(f"**{i + 1}.** {step}")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Sources & tagged volume")
        sources = overview.get("sources") or []
        if sources:
            st.bar_chart(
                {r["source"]: r["tagged_posts"] for r in sources},
                color=CHART_COLORS[0],
            )
        st.dataframe(sources, use_container_width=True, hide_index=True)

    with c2:
        st.subheader("Corpus funnel (Play Store pilot)")
        st.dataframe(overview.get("funnel") or [], use_container_width=True, hide_index=True)

    dc = overview.get("date_coverage") or {}
    st.subheader("Date coverage")
    st.write(
        f"Years **{dc.get('min_year')}–{dc.get('max_year')}** · "
        f"**{dc.get('posts_from_2021', 0):,}** tagged posts with year ≥ 2021"
    )

    with st.expander("Models used in this export", expanded=False):
        for model in overview.get("models") or meta.get("models") or []:
            st.markdown(f"- `{model}`")


def _opportunity_table(title: str, rows: list[dict], exclude_reddit: bool) -> None:
    st.subheader(title)
    filtered = []
    for r in rows:
        adj = filter_by_reddit_row(r, exclude_reddit=exclude_reddit)
        if adj:
            filtered.append(adj)
    if not filtered:
        st.caption("No rows for this slice.")
        return
    display = []
    for r in filtered:
        bs = r.get("by_source") or {}
        display.append(
            {
                "Theme": r.get("label"),
                "Posts": r.get("posts"),
                "By source": ", ".join(f"{k}:{v}" for k, v in sorted(bs.items())),
                "% relevant": r.get("pct_of_relevant"),
                "Severity": r.get("severity_avg"),
                "Branch": r.get("metric_tree_branch"),
                "Fixable?": "Yes" if r.get("fixable_under_guardrails") else "No",
                "Score": r.get("opportunity_score"),
                "Quote 1": (r.get("quotes") or ["—"])[0][:100],
                "Quote 2": (r.get("quotes") or ["—", "—"])[1][:100]
                if len(r.get("quotes") or []) > 1
                else "—",
            }
        )
    st.dataframe(display, use_container_width=True, hide_index=True)


def page_opportunities() -> None:
    meta, counts, exclude_reddit = _require_bundle()
    opp = load_json("opportunities.json")
    mix = source_mix_line(counts["by_source"], relevant=True)
    st.title(f"Opportunity ranking (relevant {mix})")
    render_data_banner(meta, counts)
    st.caption(f"Denominator: relevant posts (n={counts['relevant']:,})")
    _opportunity_table("Drop causes (drop_trigger)", opp.get("causes") or [], exclude_reddit)
    _opportunity_table(
        "Problems after stopping (lapse_type)",
        opp.get("problems_after_stopping") or [],
        exclude_reddit,
    )
    st.subheader("App usability sub-themes (replaces vague “hard to use” buckets)")
    sub = opp.get("usability_subthemes") or []
    st.dataframe(
        [
            {
                "Sub-theme": s["label"],
                "Posts": s["posts"],
                "By source": ", ".join(f"{k}:{v}" for k, v in sorted((s.get("by_source") or {}).items())),
                "% relevant": s.get("pct_of_relevant"),
            }
            for s in sub
        ],
        hide_index=True,
        use_container_width=True,
    )


def page_questions() -> None:
    meta, counts, _exclude = _require_bundle()
    mix = source_mix_line(counts["by_source"], relevant=True)
    st.title(f"Nine discovery questions (relevant corpus: {mix})")
    render_data_banner(meta, counts)
    questions = load_json("discovery_questions.json")
    for q in questions:
        with st.expander(f"{q['id']}: {q['title']}", expanded=False):
            nums = q.get("numbers") or []
            cols = st.columns(min(3, len(nums)))
            for i, n in enumerate(nums[:3]):
                cols[i].metric(n["label"], n["value"])
            chart = q.get("chart") or []
            if chart:
                st.bar_chart({str(row["year"]): row["posts"] for row in chart})
            for j, quote in enumerate(q.get("quotes") or [], start=1):
                st.markdown(f"> **Quote {j}** ({quote.get('source')}): *{quote.get('quote')}*")


def page_metric_tree() -> None:
    meta, counts, _exclude = _require_bundle()
    mix = source_mix_line(counts["by_source"], relevant=True)
    st.title(f"Metric tree — voluntary vs involuntary by source ({mix})")
    render_data_banner(meta, counts)
    tree = load_json("metric_tree.json")
    st.markdown(f"**North star:** {tree.get('root')}")

    for branch in tree.get("branches") or []:
        with st.expander(branch.get("title") or "Branch", expanded=False):
            codes = branch.get("codes") or []
            if codes:
                st.bar_chart(
                    {c["label"][:40]: c["posts"] for c in codes[:12]},
                    color=CHART_COLORS[hash(branch["field"]) % len(CHART_COLORS)],
                )

    with st.expander("Voluntary vs involuntary lapse language", expanded=False):
        vvi = tree.get("voluntary_vs_involuntary") or {}
        st.write("Overall:", vvi.get("overall"))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**By source**")
            st.json(vvi.get("by_source") or {})
        with c2:
            st.markdown("**By tagger**")
            st.json(vvi.get("by_tagger") or {})


def page_segments() -> None:
    meta, counts, _exclude = _require_bundle()
    mix = source_mix_line(counts["by_source"], relevant=True)
    st.title(f"Segment proxies — relevant by source: {mix}")
    render_data_banner(meta, counts)
    seg = load_json("segments.json")
    st.warning(seg.get("disclaimer"))
    groups = seg.get("groups") or []
    st.dataframe(
        [
            {
                "Segment": g.get("segment_label"),
                "Relevant posts": g.get("relevant_posts"),
                "Lapse stories": g.get("lapse_stories"),
                "Confidence": "Low confidence"
                if g.get("low_confidence")
                else "Directional",
            }
            for g in groups
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.subheader("Source mix (relevant)")
    st.bar_chart(counts["by_source"] and {k: v["relevant"] for k, v in counts["by_source"].items()} or {}, color=CHART_COLORS[2])


def page_evidence() -> None:
    meta, counts, exclude_reddit = _require_bundle()
    st.title("Evidence explorer: verified quotes, severity-first")
    render_data_banner(meta, counts)
    rows = load_evidence()
    min_year = st.sidebar.slider("Minimum year", 2018, 2027, 2021)
    source_filter = st.sidebar.multiselect(
        "Source",
        sorted({r.get("source") or "unknown" for r in rows}),
        default=sorted({r.get("source") or "unknown" for r in rows}),
    )
    rel_only = st.sidebar.checkbox("Relevant only", value=True)
    filtered = [
        r
        for r in rows
        if (r.get("year") or 0) >= min_year
        and (r.get("source") in source_filter)
        and (not rel_only or r.get("relevant"))
        and (not exclude_reddit or not r.get("is_curated_reddit"))
    ]
    filtered.sort(key=lambda x: (-x.get("severity", 0), -(x.get("year") or 0)))
    st.caption(f"{len(filtered)} verified quotes (match score ≥ 80)")
    st.dataframe(
        [
            {
                "Severity": r.get("severity"),
                "Year": r.get("year"),
                "Source": r.get("source"),
                "Quote": r.get("quote"),
                "Lapse": r.get("lapse_type"),
                "Trigger": r.get("drop_trigger"),
            }
            for r in filtered[:500]
        ],
        use_container_width=True,
        hide_index=True,
    )


def _load_usage() -> dict:
    if USAGE_PATH.is_file():
        return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    return {"date": str(date.today()), "count": 0}


def _save_usage(data: dict) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_PATH.write_text(json.dumps(data), encoding="utf-8")


def _answer_from_citations(question: str, hits: list[dict]) -> str:
    if not hits:
        return (
            "The exported corpus does not contain enough similar posts to answer this. "
            "Try rephrasing or run a broader collection pass."
        )
    lines = [
        "**Not investment advice.** Summary from retrieved posts only:\n",
        f"Question: *{question}*\n",
    ]
    for i, h in enumerate(hits, start=1):
        lines.append(f"{i}. [{h.get('source')} · {h.get('year')}] {h.get('snippet') or ''}")
    lines.append("\nIf these excerpts do not address your question, the data does not cover it yet.")
    return "\n".join(lines)


def embed_query(text: str) -> np.ndarray:
    from fastembed import TextEmbedding

    if "ask_embedder" not in st.session_state:
        st.session_state.ask_embedder = TextEmbedding(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
    model = st.session_state.ask_embedder
    vec = np.asarray(list(model.embed([text])), dtype=np.float32)[0]
    norm = np.linalg.norm(vec)
    return vec / max(norm, 1e-12)


def page_ask() -> None:
    meta, counts, _exclude = _require_bundle()
    st.title("Ask the data: retrieval-only answers with citations")
    render_data_banner(meta, counts)
    st.caption(
        f"Max **{SESSION_QUESTION_CAP}** questions per session, **{DAILY_QUESTION_CAP}** per day. "
        "No fund recommendations or personal advice."
    )
    if "ask_count" not in st.session_state:
        st.session_state.ask_count = 0
    usage = _load_usage()
    today = str(date.today())
    if usage.get("date") != today:
        usage = {"date": today, "count": 0}
    question = st.text_input("Your question about SIP journeys (not 'what should I buy')")
    if st.button("Search corpus") and question.strip():
        if st.session_state.ask_count >= SESSION_QUESTION_CAP:
            st.error(f"Session limit ({SESSION_QUESTION_CAP}) reached.")
            return
        if usage["count"] >= DAILY_QUESTION_CAP:
            st.error(f"Daily limit ({DAILY_QUESTION_CAP}) reached.")
            return
        emb_pack = load_ask_embeddings()
        corpus = load_ask_corpus()
        if not emb_pack or not corpus:
            st.warning("Ask index not built — re-run export.")
            return
        ids, matrix = emb_pack
        id_to_row = {r["id"]: r for r in corpus}
        qvec = embed_query(question)
        sims = matrix @ qvec
        top_idx = np.argsort(-sims)[:8]
        hits = [id_to_row[ids[i]] for i in top_idx if ids[i] in id_to_row]
        st.session_state.ask_count += 1
        usage["count"] += 1
        _save_usage(usage)
        st.markdown(_answer_from_citations(question, hits))


def page_validation() -> None:
    meta, counts, _exclude = _require_bundle()
    st.title("Validation & limitations")
    render_data_banner(meta, counts)
    val = load_json("validation.json")
    st.markdown(f"**Filter:** {val.get('filter_prompt_version')}")
    st.markdown(f"**Tag:** {val.get('tag_prompt_version')}")

    with st.expander("Inter-model agreement (30 items)", expanded=False):
        inter = val.get("inter_model_agreement") or {}
        if inter:
            st.write(
                f"Mean field agreement **{inter.get('overall', {}).get('mean_field_agreement_pct')}%**"
            )
            st.dataframe(
                [
                    {
                        "Field": f,
                        "Agree": s.get("agree"),
                        "Total": s.get("total"),
                        "%": s.get("pct"),
                    }
                    for f, s in (inter.get("field_agreement") or {}).items()
                ],
                hide_index=True,
            )

    st.subheader(f"Human corrections ({val.get('human_corrections_path')})")
    corrections = val.get("human_corrections") or []
    if corrections:
        st.dataframe(corrections, use_container_width=True, hide_index=True)
    else:
        st.caption("No correction rows yet (add rows to data/manual/corrections.csv and re-export).")

    with st.expander("Tag audit samples (20 per suspicious rule)", expanded=False):
        audit = val.get("tag_audit") or {}
        st.caption(audit.get("rule_fix"))
        for block in audit.get("audits") or []:
            st.markdown(f"**{block.get('reason')}** (population {block.get('population')})")
            for s in block.get("samples") or []:
                st.text(f"{s.get('id')}: {(s.get('quote') or '')[:120]}")

    with st.expander("Known biases", expanded=False):
        for b in val.get("known_biases") or []:
            st.markdown(f"- {b}")


if not bundle_ready():
    st.error("Run `python -m pipeline.export` first.")
    st.stop()

pages = [
    st.Page(page_overview, title="Overview", icon="🏠", default=True),
    st.Page(page_opportunities, title="Opportunity ranking", icon="🎯"),
    st.Page(page_questions, title="Discovery questions", icon="❓"),
    st.Page(page_metric_tree, title="Metric tree", icon="🌳"),
    st.Page(page_segments, title="Segments & sources", icon="👥"),
    st.Page(page_evidence, title="Evidence explorer", icon="🔍"),
    st.Page(page_ask, title="Ask the data", icon="💬"),
    st.Page(page_validation, title="Validation", icon="✅"),
]

pg = st.navigation(pages)
pg.run()
