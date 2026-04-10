"""
streamlit_app/pages/4_HCP_Detail.py — Per-HCP investigation report + SHAP explanations.

Data sources (FastAPI only — no parquet reads):
  GET /hcps/{hcp_id}              — HCP risk profile
  GET /hcps/{hcp_id}/flags        — fired compliance flags
  GET /benchmarks/{hcp_id}        — peer benchmark comparison
  GET /hcps/{hcp_id}/investigate  — InvestigationAgent (on-demand)
"""

from __future__ import annotations

import streamlit as st

from components.api_client import APIError, get_client
from config import FLAG_LABELS, RISK_TIER_COLORS, SHAP_LABELS, TIER_ORDER

st.set_page_config(
    page_title="HCP Detail",
    layout="wide",
    page_icon="👤",
)

# ── Session state defaults ─────────────────────────────────────────────────────

if "selected_hcp_id" not in st.session_state:
    st.session_state["selected_hcp_id"] = None
if "investigation_result" not in st.session_state:
    st.session_state["investigation_result"] = None
if "previous_page" not in st.session_state:
    st.session_state["previous_page"] = "pages/3_HCP_Explorer.py"

# ── HCP ID resolution ──────────────────────────────────────────────────────────

hcp_id: str | None = st.session_state.get("selected_hcp_id")

# ── Data fetching ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_hcp_profile(hcp_id: str) -> dict:
    return get_client().get(f"/hcps/{hcp_id}")


@st.cache_data(ttl=300)
def fetch_hcp_flags(hcp_id: str) -> dict:
    return get_client().get(f"/hcps/{hcp_id}/flags")


@st.cache_data(ttl=300)
def fetch_hcp_benchmarks(hcp_id: str) -> dict:
    return get_client().get(f"/benchmarks/{hcp_id}")


def fetch_hcp_investigation(hcp_id: str) -> dict:
    """Not cached — agent endpoint with 180s timeout."""
    return get_client().get_agent(f"/hcps/{hcp_id}/investigate")


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

    # Current HCP section
    st.markdown("**CURRENT HCP**")
    if hcp_id:
        st.caption(f"Viewing: {hcp_id}")
    else:
        st.caption("No HCP selected")

    prev_page = st.session_state.get("previous_page", "pages/3_HCP_Explorer.py")
    if st.button("← Back", use_container_width=True):
        st.switch_page(prev_page)

# ── Guard: require selected HCP ───────────────────────────────────────────────

if not hcp_id:
    st.info("No HCP selected — go to HCP Explorer to select an HCP.")
    st.stop()

# ── Load static data ──────────────────────────────────────────────────────────

with st.spinner("Loading HCP profile…"):
    try:
        profile = fetch_hcp_profile(hcp_id)
    except APIError as e:
        st.error(f"API error loading profile: {e}")
        st.stop()

# Clear investigation result if HCP changed
_last_hcp = st.session_state.get("_detail_last_hcp")
if _last_hcp != hcp_id:
    st.session_state["investigation_result"] = None
    st.session_state["_detail_last_hcp"] = hcp_id

# ── Page header ────────────────────────────────────────────────────────────────

hdr_left, hdr_right = st.columns([3, 1])
with hdr_left:
    st.markdown(f"## HCP Detail — {hcp_id}")
    st.caption("Static data loads instantly · Investigation is LLM-generated on demand")

with hdr_right:
    st.markdown("")
    if st.button(
        "← Back",
        key="hdr_back",
        use_container_width=True,
    ):
        st.switch_page(st.session_state.get("previous_page", "pages/3_HCP_Explorer.py"))

    if st.button("Run investigation ↗", type="primary", use_container_width=True):
        with st.spinner("Running InvestigationAgent… ~5–30s"):
            try:
                st.session_state["investigation_result"] = fetch_hcp_investigation(hcp_id)
            except APIError as e:
                st.error(f"Investigation error: {e}")

# ── Row 1: Risk score | SHAP drivers | Peer benchmark ─────────────────────────

risk_score = float(profile.get("risk_score", 0))
risk_tier  = str(profile.get("risk_tier", "low"))
tier_color = RISK_TIER_COLORS.get(risk_tier, "#6B7280")

col_gauge, col_shap, col_bench = st.columns(3)

# ── Col 1: Risk score gauge ────────────────────────────────────────────────────

with col_gauge:
    st.markdown("#### Risk score")
    st.markdown(
        f"""
        <div style="text-align:center;padding:20px;">
          <div style="font-size:48px;font-weight:700;color:{tier_color};">
            {risk_score:.0f}
          </div>
          <div style="font-size:14px;color:{tier_color};
               text-transform:uppercase;font-weight:500;letter-spacing:0.05em;">
            {risk_tier}
          </div>
          <div style="font-size:12px;color:#9CA3AF;margin-top:4px;">out of 100</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    rule_score = float(profile.get("rule_score", 0))
    if_score   = float(profile.get("anomaly_score", profile.get("if_score", 0)))
    st.caption(f"Rule score: {rule_score:.1f} · IF score: {if_score:.2f}")

# ── Col 2: SHAP / top risk drivers ────────────────────────────────────────────

with col_shap:
    st.markdown("#### Top risk drivers")
    st.info(
        "SHAP explanations available in investigation report — "
        "click 'Run investigation' to see risk driver analysis."
    )

# ── Col 3: Peer benchmark ──────────────────────────────────────────────────────

with col_bench:
    st.markdown("#### Peer benchmark")
    try:
        bench      = fetch_hcp_benchmarks(hcp_id)
        percentile = float(bench.get("percentile_rank", 0))
        peer_avg   = float(bench.get("peer_avg_spend", bench.get("peer_avg_total_spend", 0)))
        peer_max   = float(bench.get("peer_max_spend", bench.get("peer_max_total_spend", 0)))
        hcp_spend  = float(bench.get("hcp_spend", bench.get("hcp_total_spend", 0)))

        if percentile == 0:
            st.info("Percentile rank unavailable — Athena not reachable")
        else:
            st.markdown(
                f"""
                <div style='text-align:center;padding:10px;'>
                  <div style='font-size:36px;font-weight:700;
                       color:{tier_color};'>{percentile:.0f}th</div>
                  <div style='font-size:13px;color:#6b7280;'>
                       percentile of peer spend</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        col_a, col_b, col_c = st.columns(3)
        col_a.metric(
            "This HCP", f"{hcp_spend:.2f}",
            help="Normalized spend score for this HCP (RobustScaler). "
                 "Positive = above median, negative = below median.",
        )
        col_b.metric(
            "Peer avg", f"{peer_avg:.2f}",
            help="Average normalized spend score across all HCPs "
                 "in the same peer group.",
        )
        col_c.metric(
            "Peer max", f"{peer_max:.0f}",
            help="Maximum normalized spend score in the peer group. "
                 "High values indicate outlier spenders.",
        )
        st.caption(
            "Spend values are normalized scores · "
            "Hover metrics above for definitions"
        )

    except APIError:
        st.info("Benchmark data unavailable")

st.markdown("---")

# ── Row 2: Rule flags + policy citations ──────────────────────────────────────

st.markdown("#### Rule flags + policy citations")

try:
    flags_resp = fetch_hcp_flags(hcp_id)
    fired_flags = flags_resp.get("fired_flags", [])

    flags_list = flags_resp.get("fired_flags", [])

    if flags_list:
        total_flags    = flags_resp.get("total_flags", len(flags_list))
        critical_flags = flags_resp.get("critical_flags", 0)
        high_flags     = flags_resp.get("high_flags", 0)
        st.caption(
            f"{total_flags} flag(s) · "
            f"{critical_flags} critical · {high_flags} high"
        )

        for flag_name in flags_list:
            display = FLAG_LABELS.get(flag_name, flag_name.replace("_", " ").title())
            st.error(f"**{display}**")
            # No policy citation available from this endpoint
    else:
        st.success("No compliance flags for this HCP")

except APIError:
    st.warning("Flags data unavailable")

st.markdown("---")

# ── Row 3: Investigation report ───────────────────────────────────────────────

st.markdown(
    "<div style='border:2px dashed #D1D5DB;border-radius:8px;padding:16px;'>",
    unsafe_allow_html=True,
)

st.markdown(
    "#### Investigation report "
    "<span style='font-size:13px;font-weight:400;color:#9CA3AF;'>"
    "— LLM-generated by InvestigationAgent (~5–30s)</span>",
    unsafe_allow_html=True,
)

inv = st.session_state.get("investigation_result")

if inv is None:
    st.caption("Click 'Run investigation' to generate report")
else:
    rec_action  = inv.get("recommended_action", "monitor")
    confidence  = float(inv.get("confidence_score", inv.get("risk_score", 0)) or 0)
    narrative   = inv.get("score_explanation") or inv.get("summary_narrative") or ""
    rationale   = inv.get("action_rationale", "")
    key_findings = inv.get("key_findings") or []

    _ACTION_COLORS = {
        "investigate": RISK_TIER_COLORS["critical"],
        "review":      RISK_TIER_COLORS["high"],
        "monitor":     RISK_TIER_COLORS["medium"],
        "continue":    RISK_TIER_COLORS["low"],
    }
    action_color = _ACTION_COLORS.get(rec_action, "#6B7280")

    st.markdown(
        f"<div style='font-size:18px;font-weight:700;color:{action_color};"
        f"text-transform:uppercase;margin-bottom:8px;'>"
        f"Recommended action: {rec_action}</div>",
        unsafe_allow_html=True,
    )

    if rationale:
        st.markdown(f"_{rationale}_")

    if confidence > 0:
        conf_display = confidence / 100 if confidence > 1 else confidence
        st.markdown("**Confidence**")
        st.progress(float(conf_display))

    with st.expander("Full narrative", expanded=True):
        if narrative:
            narrative_clean = (narrative
                .replace("$", "\\$")
                .replace("_", " "))
            st.markdown(narrative_clean)
        else:
            st.caption("No narrative available")

    if key_findings:
        with st.expander("Key findings"):
            for finding in key_findings:
                st.markdown(f"- {finding}")

    ts = inv.get("generated_at", "")
    if ts:
        st.caption(f"Generated: {ts[:19].replace('T', ' ')} UTC")

st.markdown("</div>", unsafe_allow_html=True)
