"""
streamlit_app/pages/5_Policy_QA.py — Natural language policy Q&A via PolicyAgent.

Data source: POST /policy/query (FastAPI only — no parquet reads).
Powered by RAG over 5 policy documents (128 chunks) via Qdrant.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

import httpx

from components.api_client import APIError, get_client
from config import API_BASE_URL, RISK_TIER_COLORS

st.set_page_config(
    page_title="Policy Q&A",
    layout="wide",
    page_icon="📋",
)

# ── Session state defaults ─────────────────────────────────────────────────────

if "policy_question" not in st.session_state:
    st.session_state["policy_question"] = ""
if "policy_history" not in st.session_state:
    st.session_state["policy_history"] = []
if "auto_submit" not in st.session_state:
    st.session_state["auto_submit"] = False

_MAX_HISTORY = 5

_EXAMPLE_QUESTIONS = [
    "What is the meal cap per person?",
    "What is the speaker FMV limit?",
    "What is the annual HCP spend cap?",
    "How does Nova Pharma policy compare to PhRMA?",
    "What constitutes a vague rationale violation?",
    "When is a speaker event considered non-compliant?",
]

# ── Data fetching ──────────────────────────────────────────────────────────────

def submit_policy_query(question: str) -> dict:
    """Not cached — agent endpoint. POST /policy/query with 120s timeout."""
    response = httpx.post(
        f"{API_BASE_URL}/policy/query",
        json={"question": question},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.caption("Nova Pharma Inc.")
    st.markdown("## 🔍 Compliance Risk AI")

    try:
        get_client().get("/health")
        st.markdown("🟢 &nbsp;API online", unsafe_allow_html=True)
    except APIError:
        st.markdown("🔴 &nbsp;API unavailable", unsafe_allow_html=True)

    st.markdown("---")

    st.page_link("pages/1_Compliance_Risk_Overview.py", label="📊 Overview", icon=None)
    st.page_link("pages/2_Rep_HCP_Network.py",          label="🕸️ Rep–HCP Network", icon=None)
    st.page_link("pages/3_HCP_Explorer.py",             label="🔎 HCP Explorer", icon=None)
    st.page_link("pages/4_HCP_Detail.py",               label="👤 HCP Detail", icon=None)
    st.page_link("pages/5_Policy_QA.py",                label="📋 Policy Q&A", icon=None)

    st.markdown("---")

    # Example questions
    st.markdown("**EXAMPLE QUESTIONS**")
    for q in _EXAMPLE_QUESTIONS:
        if st.button(q, key=f"example_{q[:20]}", use_container_width=True):
            st.session_state["policy_question"] = q
            st.session_state["auto_submit"] = True
            st.rerun()

# ── Page header ────────────────────────────────────────────────────────────────

st.markdown("## Policy Q&A")
st.caption(
    "Ask questions about Nova Pharma compliance policies · "
    "Powered by RAG over 5 policy documents"
)

# ── [A] Question input ─────────────────────────────────────────────────────────

# Auto-submit if triggered by example question click
question = st.session_state.get("policy_question", "")
if st.session_state.get("auto_submit") and question:
    st.session_state["auto_submit"] = False
    with st.spinner("Querying PolicyAgent..."):
        result = submit_policy_query(question)
        if "policy_history" not in st.session_state:
            st.session_state["policy_history"] = []
        st.session_state["policy_history"].insert(0, {
            "question":        question,
            "answer":          result.get("answer", ""),
            "confidence":      result.get("confidence", "low"),
            "citations":       result.get("relevant_chunks", []),
            "nova_vs_phrma":   result.get("nova_vs_phrma", []),
            "rule_thresholds": result.get("rule_thresholds", []),
            "data_limitations": result.get("data_limitations", []),
            "timestamp":       datetime.utcnow().isoformat(),
        })
        st.session_state["policy_history"] = st.session_state["policy_history"][:_MAX_HISTORY]
    st.rerun()

question_input = st.text_input(
    "Ask a compliance policy question",
    value=st.session_state["policy_question"],
    placeholder="e.g. What is the meal cap per person?",
    key="policy_input",
)

if st.button("Submit", type="primary", disabled=not question_input.strip()):
    question = question_input.strip()
    with st.spinner("Querying PolicyAgent... ~5s"):
        try:
            result = submit_policy_query(question)
            entry = {
                "question":  question,
                "answer":    result.get("answer", ""),
                "confidence": result.get("confidence", "low"),
                "citations":  result.get("relevant_chunks", []),
                "nova_vs_phrma": result.get("nova_vs_phrma", []),
                "rule_thresholds": result.get("rule_thresholds", []),
                "data_limitations": result.get("data_limitations", []),
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            }
            history = [entry] + st.session_state["policy_history"]
            st.session_state["policy_history"] = history[:_MAX_HISTORY]
            st.session_state["policy_question"] = question
        except APIError as e:
            st.error(f"Policy agent error: {e}")

# ── [B] Latest answer panel ────────────────────────────────────────────────────

history = st.session_state["policy_history"]

if history:
    latest = history[0]

    st.markdown("---")
    st.markdown(f"**Q: {latest['question']}**")
    st.markdown("")

    # Answer text
    answer_text = latest.get("answer", "")
    if answer_text:
        answer_clean = (answer_text
            .replace("$", "\\$")
            .replace("_", " "))
        st.markdown(answer_clean)
    else:
        st.info("No answer returned.")

    st.markdown("")

    # Confidence bar
    confidence_str = latest.get("confidence", "low")
    _CONF_MAP = {"high": 1.0, "medium": 0.6, "low": 0.3}
    _CONF_COLOR = {"high": "#16A34A", "medium": "#CA8A04", "low": "#DC2626"}
    conf_val   = _CONF_MAP.get(confidence_str, 0.3)
    conf_color = _CONF_COLOR.get(confidence_str, "#888")
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;'>"
        f"<div style='flex:1;background:#E5E7EB;border-radius:999px;height:8px;'>"
        f"<div style='background:{conf_color};width:{conf_val*100:.0f}%;"
        f"height:8px;border-radius:999px;'></div></div>"
        f"<div style='font-size:12px;color:{conf_color};font-weight:600;min-width:80px;'>"
        f"Confidence: {confidence_str}</div></div>",
        unsafe_allow_html=True,
    )

    # Citations + Nova vs PhRMA comparison
    col_cite, col_compare = st.columns(2)

    with col_cite:
        st.markdown("#### Policy citations")
        citations = latest.get("citations", [])
        if citations:
            for cit in citations:
                if isinstance(cit, dict):
                    source = cit.get("source_doc", "")
                    excerpt = cit.get("excerpt", "")
                    score   = cit.get("relevance_score", 0)
                    label   = f"**{source}**" if source else "Policy document"
                    if excerpt:
                        label += f"\n\n_{excerpt[:200]}{'…' if len(excerpt) > 200 else ''}_"
                    if score:
                        label += f"\n\nRelevance: {float(score):.2f}"
                    st.info(label)
                else:
                    st.info(str(cit))
        else:
            st.caption("No citations returned for this query.")

        # Rule thresholds (bonus)
        rule_thresholds = latest.get("rule_thresholds", [])
        if rule_thresholds:
            st.markdown("**Rule thresholds**")
            for rule in rule_thresholds:
                rule_name  = rule.get("rule_name", rule.get("rule_id", ""))
                threshold  = rule.get("threshold", "")
                authority  = rule.get("authority", "")
                st.markdown(
                    f"- **{rule_name}**: {threshold}"
                    + (f" _(source: {authority})_" if authority else "")
                )

    with col_compare:
        st.markdown("#### Nova Pharma vs PhRMA")
        nova_vs_phrma = latest.get("nova_vs_phrma", [])
        if nova_vs_phrma:
            rows = []
            for item in nova_vs_phrma:
                if isinstance(item, dict):
                    rows.append({
                        "Rule":        item.get("rule_name", ""),
                        "Nova Pharma": item.get("nova_threshold", ""),
                        "PhRMA":       item.get("phrma_threshold") or "See PhRMA Code",
                    })
            if rows:
                st.table(rows)
            else:
                st.info("Comparison not available for this query")
        else:
            st.info("Comparison not available for this query")

        # Data limitations
        limitations = latest.get("data_limitations", [])
        if limitations:
            with st.expander("Data limitations", expanded=False):
                for lim in limitations:
                    st.caption(f"• {lim}")

# ── [C] Recent queries history ─────────────────────────────────────────────────

if len(history) > 1:
    with st.expander("Recent queries", expanded=False):
        if st.button("Clear history", type="secondary"):
            st.session_state["policy_history"] = []
            st.rerun()

        for item in history:
            st.markdown(f"**{item['question']}**")
            st.caption(item.get("timestamp", ""))
            answer_preview = item.get("answer", "")[:200]
            if len(item.get("answer", "")) > 200:
                answer_preview += "…"
            st.markdown(answer_preview)
            st.markdown("---")
