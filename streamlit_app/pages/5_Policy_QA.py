"""
streamlit_app/pages/5_Policy_QA.py — Natural language policy Q&A via PolicyAgent.

Data source: POST /policy/query (FastAPI only — no parquet reads).
Powered by RAG over 5 policy documents (128 chunks) via Qdrant.
"""

from __future__ import annotations

from datetime import datetime, timezone

import re

import streamlit as st

import httpx

from components.api_client import APIError, get_client
from config import API_BASE_URL, RISK_TIER_COLORS
from utils.pdf_renderer import render_pdf_page

# ── Citation quality constants ─────────────────────────────────────────────────

# Citations below _CITATION_WEAK_THRESHOLD are hidden (noise floor).
# Citations in [_CITATION_WEAK_THRESHOLD, _CITATION_STRONG_THRESHOLD) display with
# a "weak match" warning — above the noise floor but below confident retrieval.
# Citations >= _CITATION_STRONG_THRESHOLD display normally.
# After the 1.2a embedding fix, genuine hits score 0.4–0.8; noise scores ~0.02–0.05.
_CITATION_WEAK_THRESHOLD   = 0.30
_CITATION_STRONG_THRESHOLD = 0.50


def _clean_answer(text: str) -> tuple[str, list[str]]:
    """
    Transform inline citation markers in agent answer text for UI display.

    Agent output often contains:
      [Rule ID: MEAL_002, Chunk ID: DOC_002_chunk_0000]
      [Chunk ID: DOC_002_chunk_0000]

    These are rendered as:
      - Rule ID  → small superscript badge  <sup>[MEAL 002]</sup>
      - Chunk ID → suppressed inline; collected for optional debug display

    Also applies standard Streamlit escaping ($→\\$, _→space).
    Returns (cleaned_html_string, list_of_chunk_ids_found).
    """
    chunk_ids: list[str] = []

    def _replace_full(m: re.Match) -> str:
        rule_id  = (m.group(1) or "").strip()
        chunk_id = (m.group(2) or "").strip()
        if chunk_id:
            chunk_ids.append(chunk_id)
        if rule_id:
            return f'<sup style="color:#6B7280;font-size:0.75em;">[{rule_id}]</sup>'
        return ""

    def _replace_chunk_only(m: re.Match) -> str:
        chunk_ids.append(m.group(1).strip())
        return ""

    cleaned = re.sub(
        r'\[Rule\s+ID:\s*([A-Z_0-9]+)(?:,\s*Chunk\s+ID:\s*([A-Za-z0-9_]+))?\]',
        _replace_full, text, flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r'\[Chunk\s+ID:\s*([A-Za-z0-9_]+)\]',
        _replace_chunk_only, cleaned, flags=re.IGNORECASE,
    )
    # Standard Streamlit escaping — applied after HTML substitution so that
    # underscores in rule IDs inside HTML tags are also converted to spaces.
    cleaned = cleaned.replace("$", "\\$").replace("_", " ")
    return cleaned, chunk_ids

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

    # Answer text — inline citations transformed: rule IDs as superscript,
    # chunk IDs suppressed from prose (shown in debug expander below).
    answer_text = latest.get("answer", "")
    if answer_text:
        answer_clean, _inline_chunk_ids = _clean_answer(answer_text)
        st.markdown(answer_clean, unsafe_allow_html=True)
        if _inline_chunk_ids:
            with st.expander("Debug: chunk IDs cited inline", expanded=False):
                st.caption(", ".join(sorted(set(_inline_chunk_ids))))

        # Grounding indicator — informational line showing which sources contributed.
        _rule_ids = [
            r.get("rule_id", "")
            for r in latest.get("rule_thresholds", [])
            if r.get("rule_id")
        ]
        _n_chunks = len(latest.get("citations", []))
        if _rule_ids and _n_chunks:
            _grounding = (
                f"Grounded in: rules registry [{', '.join(_rule_ids)}]"
                f" + retrieval ({_n_chunks} chunk{'s' if _n_chunks != 1 else ''})"
            )
        elif _rule_ids:
            _grounding = f"Grounded in: rules registry [{', '.join(_rule_ids)}]"
        elif _n_chunks:
            _grounding = f"Grounded in: retrieval ({_n_chunks} chunk{'s' if _n_chunks != 1 else ''})"
        else:
            _grounding = "Grounded in: no specific source"
        st.caption(_grounding)
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
        # Deduplication is handled at the agent layer (_parse_citations uses a
        # seen-set keyed on chunk_id), so the list here is already deduplicated.
        citations = latest.get("citations", [])
        if citations:
            _any_shown = False
            for _cit_idx, cit in enumerate(citations):
                if isinstance(cit, dict):
                    source   = cit.get("source_doc", "")
                    excerpt  = cit.get("excerpt", "")
                    score    = float(cit.get("relevance_score", 0))
                    chunk_id = cit.get("chunk_id", str(_cit_idx))
                    _pdf_url = f"/app/static/policy_docs/{source}" if source else None

                    if score < _CITATION_WEAK_THRESHOLD:
                        continue  # below noise floor — hide

                    _any_shown = True
                    # Document name as clickable link; excerpt removed (whole-document
                    # link in the expander now serves as fallback for context).
                    _doc_link = f"**[{source}]({_pdf_url})**" if _pdf_url else "**Policy document**"

                    if score < _CITATION_STRONG_THRESHOLD:
                        # Weak match: above noise floor but below confident retrieval.
                        st.info(
                            f"⚠ **Weak match** (score {score:.2f} — low relevance;"
                            f" treat with caution)\n\n" + _doc_link
                        )
                    else:
                        # Strong match: confident retrieval.
                        st.info(f"{_doc_link}\n\nRelevance: {score:.2f}")

                    # Inline source page viewer with highlighting + multi-page navigation
                    _original_page = cit.get("page_num")
                    if _original_page is not None and source:
                        _vkey = f"viewer_page_{_cit_idx}_{chunk_id}"
                        _ekey = f"viewer_expanded_{_cit_idx}_{chunk_id}"
                        if _vkey not in st.session_state:
                            st.session_state[_vkey] = _original_page
                        if _ekey not in st.session_state:
                            st.session_state[_ekey] = False
                        _cur_page = st.session_state[_vkey]

                        with st.expander(
                            f"View source · p. {_cur_page}",
                            expanded=st.session_state[_ekey],
                        ):
                            # Immediately reset expanded flag so future reruns (from
                            # unrelated interactions) don't force this expander open.
                            # Navigation buttons set it True again before rerun.
                            st.session_state[_ekey] = False

                            # Determine chunk_text for highlighting:
                            # - Original page: search the chunk prefix (start of chunk)
                            # - Continuation page: search the tail half of the chunk text,
                            #   since the first half was already on the previous page.
                            #   Guard: only apply tail-search if chunk is long enough
                            #   that splitting is meaningful (> 200 chars).
                            _on_original = (_cur_page == _original_page)
                            if excerpt and not _on_original and len(excerpt) > 200:
                                _chunk_text = excerpt[len(excerpt) // 2:]
                            else:
                                _chunk_text = excerpt or None

                            _result = render_pdf_page(
                                source, _cur_page, chunk_text=_chunk_text
                            )
                            if _result is not None:
                                _img_bytes, _meta = _result
                                st.image(_img_bytes, use_container_width=True)

                                # Caption based on highlight outcome and page position
                                _hs = _meta["highlight_status"]
                                _doc_label = source.replace(".pdf", "").replace("_", " ")
                                if _on_original:
                                    if _hs == "full":
                                        _caption = f"Page {_cur_page} of {_doc_label}"
                                    elif _hs == "none":
                                        _caption = (
                                            f"Page {_cur_page} of {_doc_label}"
                                            " · unable to highlight chunk"
                                        )
                                    else:
                                        _caption = (
                                            f"Page {_cur_page} of {_doc_label}"
                                            " · highlighted partial match"
                                        )
                                else:
                                    # Continuation page — tell the user what they're looking at
                                    if _hs == "none":
                                        _caption = (
                                            f"Page {_cur_page} of {_doc_label}"
                                            " · chunk continues here (no exact text match)"
                                        )
                                    else:
                                        _caption = (
                                            f"Page {_cur_page} of {_doc_label}"
                                            f" · continuation of chunk from page {_original_page}"
                                        )
                                st.caption(_caption)
                                if _pdf_url:
                                    st.markdown(
                                        f'<a href="{_pdf_url}" target="_blank"'
                                        f' style="font-size:0.8em;">📄 Open full document in new tab</a>',
                                        unsafe_allow_html=True,
                                    )

                                # Forward navigation: only on the original page.
                                # Cap at original_page + 1 to prevent unbounded navigation
                                # past where the chunk ends.
                                if _meta.get("chunk_continues") and _on_original:
                                    if st.button(
                                        f"Continued on page {_cur_page + 1} →",
                                        key=f"nav_fwd_{_vkey}",
                                    ):
                                        st.session_state[_vkey] = _cur_page + 1
                                        st.session_state[_ekey] = True
                                        st.rerun()

                                # Back navigation: return to original citation page
                                if _cur_page != _original_page:
                                    if st.button(
                                        f"← Back to page {_original_page}",
                                        key=f"nav_back_{_vkey}",
                                    ):
                                        st.session_state[_vkey] = _original_page
                                        st.session_state[_ekey] = True
                                        st.rerun()
                            else:
                                st.caption("Source page not available.")
                else:
                    _any_shown = True
                    st.info(str(cit))
            if not _any_shown:
                st.caption("No citations above relevance threshold for this query.")
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
        # Filter to rules the agent actually used (rule_ids_matched derived from
        # rule_thresholds, which carries the same set of rule_ids the agent looked up).
        _rule_ids_matched = {
            r.get("rule_id")
            for r in latest.get("rule_thresholds", [])
            if r.get("rule_id")
        }
        _filtered_comparisons = [
            item for item in nova_vs_phrma
            if isinstance(item, dict) and item.get("source_rule_id") in _rule_ids_matched
        ] if nova_vs_phrma and _rule_ids_matched else []

        if _filtered_comparisons:
            rows = [
                {
                    "Rule":        item.get("rule_name", ""),
                    "Nova Pharma": item.get("nova_threshold", ""),
                    "PhRMA":       item.get("phrma_threshold") or "See PhRMA Code",
                }
                for item in _filtered_comparisons
            ]
            st.table(rows)
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
