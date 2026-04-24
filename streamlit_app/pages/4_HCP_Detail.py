"""
streamlit_app/pages/4_HCP_Detail.py — Per-HCP investigation report + SHAP explanations.

Data sources (FastAPI only — no parquet reads):
  GET /hcps/{hcp_id}              — HCP risk profile
  GET /hcps/{hcp_id}/flags        — fired compliance flags
  GET /benchmarks/{hcp_id}        — peer benchmark comparison
  GET /hcps/{hcp_id}/investigate  — InvestigationAgent (on-demand)
"""

from __future__ import annotations

import plotly.graph_objects as go
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


@st.cache_data(ttl=300)
def fetch_hcp_shap(hcp_id: str, top_n: int = 10) -> dict:
    return get_client().get(f"/hcps/{hcp_id}/shap", params={"top_n": top_n})


def extract_tov(profile: dict) -> dict:
    """Extract ToV fields from HCP profile API response."""
    return {
        "nova_tov_2022":          profile.get("nova_tov_2022", 0) or 0,
        "nova_tov_2023":          profile.get("nova_tov_2023", 0) or 0,
        "nova_tov_2024":          profile.get("nova_tov_2024", 0) or 0,
        "nova_food_beverage_2022": profile.get("nova_food_beverage_2022", 0) or 0,
        "nova_food_beverage_2023": profile.get("nova_food_beverage_2023", 0) or 0,
        "nova_food_beverage_2024": profile.get("nova_food_beverage_2024", 0) or 0,
        "nova_speaking_fee_2022":  profile.get("nova_speaking_fee_2022", 0) or 0,
        "nova_speaking_fee_2023":  profile.get("nova_speaking_fee_2023", 0) or 0,
        "nova_speaking_fee_2024":  profile.get("nova_speaking_fee_2024", 0) or 0,
        "nova_consulting_2022":    profile.get("nova_consulting_2022", 0) or 0,
        "nova_consulting_2023":    profile.get("nova_consulting_2023", 0) or 0,
        "nova_consulting_2024":    profile.get("nova_consulting_2024", 0) or 0,
        "nova_travel_2022":        profile.get("nova_travel_2022", 0) or 0,
        "nova_travel_2023":        profile.get("nova_travel_2023", 0) or 0,
        "nova_travel_2024":        profile.get("nova_travel_2024", 0) or 0,
        "total_tov_2022":          profile.get("total_tov_all_companies_2022", 0) or 0,
        "total_tov_2023":          profile.get("total_tov_all_companies_2023", 0) or 0,
        "total_tov_2024":          profile.get("total_tov_all_companies_2024", 0) or 0,
        "nova_sow_2022":           profile.get("nova_sow_2022", 0) or 0,
        "nova_sow_2023":           profile.get("nova_sow_2023", 0) or 0,
        "nova_sow_2024":           profile.get("nova_sow_2024", 0) or 0,
    }


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

_STATE_NAMES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
    "CO":"Colorado","CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia",
    "HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa",
    "KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland",
    "MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi",
    "MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire",
    "NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina",
    "ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania",
    "RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee",
    "TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington",
    "WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming","DC":"Washington D.C.",
}

hdr_left, hdr_right = st.columns([3, 1])
with hdr_left:
    st.markdown(f"## HCP Detail — {hcp_id}")

    _spec      = profile.get("specialty") or ""
    _state_raw = profile.get("state") or ""
    _state     = _STATE_NAMES.get(_state_raw.upper(), _state_raw)
    _spec_state = " · ".join(x for x in [_spec, _state] if x)
    if _spec_state:
        st.caption(_spec_state)

    # Derive last engagement year from nova ToV fields
    _tov_by_year = {
        2024: float(profile.get("nova_tov_2024", 0) or 0),
        2023: float(profile.get("nova_tov_2023", 0) or 0),
        2022: float(profile.get("nova_tov_2022", 0) or 0),
    }
    _last_eng = next(
        (yr for yr in [2024, 2023, 2022] if _tov_by_year[yr] > 0),
        None
    )
    _eng_str = f"Last Nova engagement: {_last_eng}" if _last_eng else "No Nova engagement on record"

    st.caption(
        f"Static data loads instantly · "
        f"Investigation is LLM-generated on demand · {_eng_str}"
    )

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

# Note on tooltip overlap: the TOP RISK DRIVERS ℹ️ tooltip (col_shap, below) says
# "SHAP feature importance scores show which factors most influenced this HCP risk
# score" — this partially duplicates the expander's "Top Risk Drivers" paragraph.
# The RISK SCORE ℹ️ tooltip also lightly overlaps the expander's closing sentence.
# Both tooltips are technical; the expander is plain-English for business users.
# A future consolidation pass could remove the tooltips and fold their content here.
with st.expander("ⓘ How to read this page", expanded=False):
    st.markdown(
        "**Rule Flags** show specific policy violations this HCP triggered. "
        "Each flag corresponds to a compliance threshold the HCP crossed — "
        "for example, exceeding the meal limit or receiving speaker fees "
        "above fair market value. These are explicit, auditable violations of "
        "Nova Pharma and PhRMA policy.\n\n"
        "**Top Risk Drivers** show statistical patterns in this HCP's "
        "behavior that our anomaly detection model identified as unusual. "
        "These are not policy violations on their own — they indicate that "
        "this HCP's profile (spend levels, rep interactions, engagement "
        "patterns) stands out compared to similar HCPs. Unusual statistical "
        "behavior is worth reviewing even when no rules have fired.\n\n"
        "A high-risk HCP typically has both: specific policy violations and "
        "unusual statistical patterns. The overall Risk Score combines both "
        "signals to prioritize investigation."
    )

# ── Pre-compute ToV so it's available in Row 1 peer benchmark fallback ────────

spend = extract_tov(profile)

# ── Pre-fetch flag counts for compact banner ──────────────────────────────────

risk_score = float(profile.get("risk_score", 0))
risk_tier  = str(profile.get("risk_tier", "low"))
tier_color = RISK_TIER_COLORS.get(risk_tier, "#6B7280")
rule_score = float(profile.get("rule_score", 0))
if_score   = float(profile.get("anomaly_score", profile.get("if_score", 0)))

try:
    _flags_resp  = fetch_hcp_flags(hcp_id)
    _n_flags     = _flags_resp.get("total_flags", len(_flags_resp.get("fired_flags", [])))
    _n_critical  = _flags_resp.get("critical_flags", 0)
    _n_high      = _flags_resp.get("high_flags", 0)
except APIError:
    _n_flags = _n_critical = _n_high = 0

try:
    bench      = fetch_hcp_benchmarks(hcp_id)
    percentile = float(bench.get("percentile_rank", 0))
    peer_avg   = float(bench.get("peer_avg_spend", bench.get("peer_avg_total_spend", 0)))
    peer_max   = float(bench.get("peer_max_spend", bench.get("peer_max_total_spend", 0)))
    hcp_spend  = float(bench.get("hcp_spend", bench.get("hcp_total_spend", 0)))
    _bench_ok  = True
except APIError:
    percentile = peer_avg = peer_max = hcp_spend = 0.0
    _bench_ok  = False

# ── Row 1: Scorecard panel ────────────────────────────────────────────────────

_CARD = (
    "border:1px solid rgba(255,255,255,0.12);border-radius:10px;"
    "padding:20px 24px;background:transparent;"
    "height:100%;"
)
_LABEL = "font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:inherit;opacity:0.6;font-weight:600;"

st.markdown("""
<style>
.tip {
    position: relative;
    cursor: help;
    display: inline-block;
}
.tip::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: 125%;
    left: 50%;
    transform: translateX(-50%);
    background: #1f2937;
    color: #f9fafb;
    font-size: 12px;
    font-weight: 400;
    line-height: 1.4;
    padding: 6px 10px;
    border-radius: 6px;
    white-space: normal;
    width: 220px;
    text-align: left;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.15s ease;
    z-index: 9999;
}
.tip:hover::after {
    opacity: 1;
}
</style>
""", unsafe_allow_html=True)

col_risk, col_shap, col_bench = st.columns([1, 1.2, 1])

with col_risk:
    st.markdown(
        f"<div style='{_CARD}'>"
        f"<div style='{_LABEL}'>RISK SCORE &nbsp;"
        f"<span class='tip' data-tooltip='Composite score combining rule-based compliance "
        f"flags (0-100) and Isolation Forest anomaly detection. Higher = more risk.'>ℹ️</span></div>"
        f"<div style='font-size:48px;font-weight:800;color:{tier_color};line-height:1.1;margin-top:6px;'>"
        f"<span class='tip' data-tooltip='Risk score: {risk_score:.1f} out of 100'>"
        f"{risk_score:.0f}</span></div>"
        f"<div style='font-size:13px;font-weight:700;color:{tier_color};"
        f"text-transform:uppercase;margin-top:2px;'>"
        f"<span class='tip' data-tooltip='Thresholds: Low under 30 · Medium 30-59 · "
        f"High 60-79 · Critical 80 and above'>{risk_tier}</span></div>"
        f"<div style='font-size:13px;color:#6b7280;margin-top:8px;'>"
        f"Rule score: <span class='tip' data-tooltip='Weighted sum of flag severities: "
        f"Critical=3pts · High=2pts · Medium=1pt'>{rule_score:.0f}</span>"
        f" &nbsp;·&nbsp; "
        f"IF score: <span class='tip' data-tooltip='Isolation Forest anomaly score: higher "
        f"values indicate more anomalous spend vs peer group'>{if_score:.2f}</span></div>"
        f"<div style='font-size:12px;margin-top:4px;'>"
        f"<span class='tip' data-tooltip='{_n_flags} total · {_n_critical} critical · "
        f"{_n_high} high · {_n_flags - _n_critical - _n_high} medium'>"
        f"<b>{_n_flags}</b> flag(s) &nbsp;·&nbsp; "
        f"<span style='color:#DC2626;font-weight:700;'>{_n_critical} critical</span> &nbsp;·&nbsp; "
        f"<span style='color:#EA580C;font-weight:700;'>{_n_high} high</span>"
        f"</span></div></div>",
        unsafe_allow_html=True,
    )

with col_shap:
    st.markdown(
        f"<div style='{_LABEL}'>TOP RISK DRIVERS &nbsp;"
        f"<span class='tip' data-tooltip='SHAP feature importance scores show which factors "
        f"most influenced this HCP risk score'>ℹ️</span></div>",
        unsafe_allow_html=True,
    )
    try:
        _shap_data = fetch_hcp_shap(hcp_id, top_n=7)
        _top_features = _shap_data.get("top_features", [])
        if _top_features:
            _feat_names = [f["feature"].replace("_", " ").title() for f in reversed(_top_features)]
            _shap_vals  = [f["shap_value"] for f in reversed(_top_features)]
            _bar_colors = ["#DC2626" if v < 0 else "#16A34A" for v in _shap_vals]
            fig_shap = go.Figure(go.Bar(
                x=_shap_vals,
                y=_feat_names,
                orientation="h",
                marker_color=_bar_colors,
            ))
            fig_shap.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                height=280,
                margin=dict(l=0, r=0, t=4, b=0),
                xaxis=dict(
                    gridcolor="rgba(0,0,0,0.08)",
                    tickfont=dict(color="#374151"),
                ),
                yaxis=dict(
                    tickfont=dict(color="#374151", size=10),
                    showgrid=False,
                ),
                font=dict(color="#374151"),
            )
            st.plotly_chart(fig_shap, use_container_width=True)
        else:
            st.caption("No SHAP data available for this HCP.")
    except APIError:
        st.caption("SHAP values unavailable for this HCP.")

with col_bench:
    if _bench_ok and percentile > 0:
        _bench_tip = "Peer group = HCPs with same specialty and state. Spend is Nova Pharma transfer of value only."
        _bench_body = (
            f"<div style='display:flex;gap:12px;margin-top:10px;'>"

            f"<div style='flex:1;background:rgba(255,255,255,0.08);border-radius:8px;"
            f"padding:14px 12px;text-align:center;'>"
            f"<div style='font-size:11px;font-weight:600;color:inherit;opacity:0.7;"
            f"text-transform:uppercase;letter-spacing:0.05em;'>Percentile</div>"
            f"<div style='font-size:36px;font-weight:800;color:#1d4ed8;"
            f"line-height:1.1;margin-top:4px;'>"
            f"<span class='tip' data-tooltip='{percentile:.0f}th percentile: "
            f"this HCP Nova spend exceeds {percentile:.0f}% of peers "
            f"in same specialty and state'>{percentile:.0f}th</span></div>"
            f"<div style='font-size:11px;color:inherit;opacity:0.5;margin-top:2px;'>"
            f"of peer spend</div></div>"

            f"<div style='flex:1;background:rgba(255,255,255,0.08);border-radius:8px;"
            f"padding:14px 12px;text-align:center;'>"
            f"<div style='font-size:11px;font-weight:600;color:inherit;opacity:0.7;"
            f"text-transform:uppercase;letter-spacing:0.05em;'>Nova Spend</div>"
            f"<div style='font-size:36px;font-weight:800;color:inherit;"
            f"line-height:1.1;margin-top:4px;'>"
            f"<span class='tip' data-tooltip='Total Nova Pharma transfer of value "
            f"to this HCP in 2024 (CMS open payments data)'>"
            f"${hcp_spend:.0f}</span></div>"
            f"<div style='font-size:11px;color:inherit;opacity:0.5;margin-top:2px;'>"
            f"2024 CMS dollars</div></div>"

            f"<div style='flex:1;background:rgba(255,255,255,0.08);border-radius:8px;"
            f"padding:14px 12px;text-align:center;'>"
            f"<div style='font-size:11px;font-weight:600;color:inherit;opacity:0.7;"
            f"text-transform:uppercase;letter-spacing:0.05em;'>Peer Avg</div>"
            f"<div style='font-size:36px;font-weight:800;color:inherit;"
            f"line-height:1.1;margin-top:4px;'>"
            f"<span class='tip' data-tooltip='Average Nova spend across "
            f"{bench.get('peer_count', '')} peers "
            f"in same specialty and state. Peer max: ${peer_max:.0f}'>"
            f"${peer_avg:.0f}</span></div>"
            f"<div style='font-size:11px;color:inherit;opacity:0.5;margin-top:2px;'>"
            f"same specialty/state</div></div>"

            f"</div>"
        )
    else:
        _bench_tip = "Athena unavailable. Showing normalized spend index and real Nova ToV from CMS open payments data."
        _bench_body = (
            f"<div style='font-size:12px;color:inherit;opacity:0.6;margin-top:6px;'>Normalized index</div>"
            f"<div style='font-size:40px;font-weight:800;color:inherit;line-height:1.1;'>"
            f"<span class='tip' data-tooltip='Normalized spend index vs peer group. "
            f"Athena re-run needed for percentile rank and dollar benchmarks.'>"
            f"{peer_avg:.2f}</span></div>"
            f"<div style='font-size:12px;color:inherit;opacity:0.5;margin-top:2px;'>vs peer group</div>"
            f"<div style='display:inline-flex;gap:16px;margin-top:12px;'>"
            f"<div><div style='font-size:11px;color:inherit;opacity:0.5;'>2022</div>"
            f"<div style='font-size:16px;font-weight:700;color:inherit;'>"
            f"<span class='tip' data-tooltip='Nova Pharma payments to this HCP in 2022 (CMS data)'>"
            f"${spend['nova_tov_2022']:.0f}</span></div></div>"
            f"<div><div style='font-size:11px;color:inherit;opacity:0.5;'>2023</div>"
            f"<div style='font-size:16px;font-weight:700;color:inherit;'>"
            f"<span class='tip' data-tooltip='Nova Pharma payments to this HCP in 2023 (CMS data)'>"
            f"${spend['nova_tov_2023']:.0f}</span></div></div>"
            f"<div><div style='font-size:11px;color:inherit;opacity:0.5;'>2024</div>"
            f"<div style='font-size:16px;font-weight:700;color:inherit;'>"
            f"<span class='tip' data-tooltip='Nova Pharma payments to this HCP in 2024 (CMS data)'>"
            f"${spend['nova_tov_2024']:.0f}</span></div></div>"
            f"</div>"
            f"<div style='font-size:11px;color:inherit;opacity:0.5;margin-top:8px;'>Real Nova ToV · CMS data</div>"
        )
    st.markdown(
        f"<div style='{_CARD}'>"
        f"<div style='{_LABEL}'>PEER BENCHMARK &nbsp;"
        f"<span class='tip' data-tooltip='{_bench_tip}'>ℹ️</span></div>"
        f"{_bench_body}"
        f"</div>",
        unsafe_allow_html=True,
    )

st.markdown(
    "<hr style='margin:8px 0 16px 0;border:none;border-top:1px solid #e5e7eb;'>",
    unsafe_allow_html=True,
)

# ── Row 2: Flags | ToV chart | SOW ────────────────────────────────────────────

col_flags, col_tov, col_sow = st.columns([1, 1.5, 1])

with col_flags:
    st.markdown("#### Rule Flags")
    try:
        flags_resp     = fetch_hcp_flags(hcp_id)
        flags_list     = flags_resp.get("fired_flags", [])
        if flags_list:
            total_flags    = flags_resp.get("total_flags", len(flags_list))
            critical_flags = flags_resp.get("critical_flags", 0)
            high_flags     = flags_resp.get("high_flags", 0)
            st.caption(f"{total_flags} flag(s) · {critical_flags} critical · {high_flags} high")
            _FLAG_TOOLTIPS = {
                "speaker_pay_above_fmv": (
                    "Speaker fee paid exceeded Nova FMV limit. "
                    "Nova policy: fees must not exceed specialty-based "
                    "FMV rate card. PhRMA cap: $1,500/event. "
                    "Check investigation report for exact amounts."
                ),
                "chronic_speaker_fmv_violations": (
                    "Pattern of FMV violations across 3+ speaking events. "
                    "Nova policy: 2+ FMV breaches in a program year "
                    "triggers chronic violation flag. Requires review."
                ),
                "repeat_speaker_pattern": (
                    "HCP engaged as speaker beyond frequency threshold. "
                    "Nova policy: max 6 speaking engagements per HCP "
                    "per program year. PhRMA guidance: avoid patterns "
                    "that suggest improper intent."
                ),
                "rapid_repeat_engagements": (
                    "Multiple interactions within 30 days. "
                    "Nova policy: interactions must be spaced to reflect "
                    "legitimate educational need. Dense clustering "
                    "may indicate coordination risk."
                ),
                "missing_interaction_attestation": (
                    "One or more interactions missing required attestation. "
                    "Nova policy: all HCP interactions must be attested "
                    "within 5 business days. Missing attestation is a "
                    "documentation compliance violation."
                ),
                "chronic_missing_attestations": (
                    "Pattern of missing attestations across 3+ interactions. "
                    "Nova policy: repeated failures indicate systematic "
                    "documentation gap requiring manager review."
                ),
                "vague_interaction_rationale": (
                    "Interaction rationale lacks required specificity. "
                    "Nova policy: rationale must state therapeutic area, "
                    "educational objective, and HCP relevance. "
                    "Generic entries like 'discussed products' do not qualify."
                ),
                "pattern_of_vague_rationales": (
                    "Repeated vague rationales across 3+ interactions. "
                    "Nova policy: systematic documentation gaps require "
                    "rep coaching and compliance review."
                ),
                "fmv_non_compliance": (
                    "Total transfer of value exceeds FMV limits. "
                    "PhRMA guidelines: aggregate spend must reflect "
                    "fair market value for services rendered. "
                    "Nova internal threshold is stricter than PhRMA baseline."
                ),
                "escalating_spend_pattern": (
                    "Year-over-year Nova spend increasing abnormally. "
                    "Nova policy: spend escalation without corresponding "
                    "increase in documented educational activity "
                    "triggers compliance review."
                ),
            }
            for i, flag_name in enumerate(flags_list):
                if i < critical_flags:
                    bg_color = "rgba(220,38,38,0.12)"; border_color = "#DC2626"
                    text_color = "#f87171"; severity_badge = "CRITICAL"; badge_bg = "#DC2626"
                elif i < critical_flags + high_flags:
                    bg_color = "rgba(234,88,12,0.12)"; border_color = "#EA580C"
                    text_color = "#fb923c"; severity_badge = "HIGH"; badge_bg = "#EA580C"
                else:
                    bg_color = "rgba(202,138,4,0.12)"; border_color = "#CA8A04"
                    text_color = "#fbbf24"; severity_badge = "MEDIUM"; badge_bg = "#CA8A04"
                display = FLAG_LABELS.get(flag_name, flag_name.replace("_", " ").title())
                _tip = _FLAG_TOOLTIPS.get(flag_name, f"Compliance flag: {display}")
                st.markdown(
                    f"""<div class='tip' data-tooltip='{_tip}'
                    style='padding:6px 10px;margin-bottom:4px;border-radius:4px;
                    background:{bg_color};border-left:3px solid {border_color};
                    cursor:help;'>
                    <span style='font-size:10px;font-weight:700;color:#ffffff;
                    background:{badge_bg};padding:1px 5px;border-radius:3px;
                    margin-right:6px;'>{severity_badge}</span>
                    <span style='font-size:12px;font-weight:600;color:{text_color};'>
                    {display}</span></div>""",
                    unsafe_allow_html=True,
                )
        else:
            st.success("No compliance flags")
    except APIError:
        st.warning("Flags data unavailable")

with col_tov:
    st.markdown("#### Transfer of Value")
    if not any(spend.values()):
        st.info("Spend history unavailable")
    else:
        years = ["2022", "2023", "2024"]
        all_totals = [spend["total_tov_2022"], spend["total_tov_2023"], spend["total_tov_2024"]]
        active_mask = [t > 0 for t in all_totals]
        active_years = [y for y, m in zip(years, active_mask) if m]

        if not active_years:
            st.info("No CMS payment records found for this HCP")
        else:
            food_all      = [spend["nova_food_beverage_2022"], spend["nova_food_beverage_2023"], spend["nova_food_beverage_2024"]]
            speaking_all  = [spend["nova_speaking_fee_2022"],  spend["nova_speaking_fee_2023"],  spend["nova_speaking_fee_2024"]]
            consulting_all= [spend["nova_consulting_2022"],    spend["nova_consulting_2023"],    spend["nova_consulting_2024"]]
            travel_all    = [spend["nova_travel_2022"],        spend["nova_travel_2023"],        spend["nova_travel_2024"]]
            other_all     = [max(0, spend[f"total_tov_{y}"] - spend[f"nova_tov_{y}"]) for y in ["2022","2023","2024"]]

            food       = [v for v, m in zip(food_all,       active_mask) if m]
            speaking   = [v for v, m in zip(speaking_all,   active_mask) if m]
            consulting = [v for v, m in zip(consulting_all, active_mask) if m]
            travel     = [v for v, m in zip(travel_all,     active_mask) if m]
            other      = [v for v, m in zip(other_all,      active_mask) if m]
            totals     = [v for v, m in zip(all_totals,     active_mask) if m]

            fig_spend = go.Figure()
            fig_spend.add_trace(go.Bar(
                x=active_years, y=food,
                name="Nova — Meals & Food", marker_color="#185FA5",
                text=[f"${v:,.0f}" if v > 0 else "" for v in food],
                textposition="inside", textfont=dict(size=11, color="#ffffff", family="Arial Bold"),
            ))
            fig_spend.add_trace(go.Bar(
                x=active_years, y=speaking,
                name="Nova — Speaking Fees", marker_color="#DC2626",
                text=[f"${v:,.0f}" if v > 0 else "" for v in speaking],
                textposition="inside", textfont=dict(size=11, color="#ffffff", family="Arial Bold"),
            ))
            fig_spend.add_trace(go.Bar(
                x=active_years, y=consulting,
                name="Nova — Consulting", marker_color="#CA8A04",
                text=[f"${v:,.0f}" if v > 0 else "" for v in consulting],
                textposition="inside", textfont=dict(size=11, color="#ffffff", family="Arial Bold"),
            ))
            fig_spend.add_trace(go.Bar(
                x=active_years, y=travel,
                name="Nova Travel", marker_color="#059669",
                text=[f"${v:,.0f}" if v > 0 else "" for v in travel],
                textposition="inside", textfont=dict(size=11, color="#ffffff", family="Arial Bold"),
            ))
            fig_spend.add_trace(go.Bar(
                x=active_years, y=other,
                name="Other Companies", marker_color="rgba(148,163,184,0.6)",
                text=[f"${v:,.0f}" if v > 0 else "" for v in other],
                textposition="inside", textfont=dict(size=11, color="#374151", family="Arial Bold"),
            ))
            fig_spend.update_layout(
                barmode="stack",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=40, b=0),
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=-0.25,
                    xanchor="center",
                    x=0.5,
                    bgcolor="rgba(0,0,0,0)",
                    font=dict(size=12, color="#374151"),
                ),
                xaxis=dict(
                    tickfont=dict(size=14, color="#374151", family="Arial Bold"),
                    showgrid=False,
                ),
                yaxis=dict(
                    tickfont=dict(size=13, color="#374151"),
                    tickprefix="$",
                    gridcolor="rgba(0,0,0,0.08)",
                    showgrid=True,
                ),
                font=dict(color="#374151"),
                annotations=[
                    dict(
                        x=year, y=total,
                        text=f"<b>Total: ${total:,.0f}</b>",
                        showarrow=False,
                        yanchor="bottom",
                        yshift=6,
                        font=dict(size=13, color="#374151", family="Arial Bold"),
                    )
                    for year, total in zip(active_years, totals)
                ],
            )
            st.plotly_chart(fig_spend, use_container_width=True)

with col_sow:
    st.markdown("#### Share of Wallet")
    if any(spend.values()):
        sow_values = [
            ("2022", spend["nova_sow_2022"]),
            ("2023", spend["nova_sow_2023"]),
            ("2024", spend["nova_sow_2024"]),
        ]
        for year, sow in sow_values:
            nova  = spend[f"nova_tov_{year}"]
            total = spend[f"total_tov_{year}"]
            color = (
                "#DC2626" if sow >= 0.5  else
                "#EA580C" if sow >= 0.25 else
                "#CA8A04" if sow >= 0.1  else
                "#16A34A"
            )
            st.markdown(
                f"""<div style='margin-bottom:10px;padding:10px;border-radius:6px;
                border-left:4px solid {color};background:rgba(0,0,0,0.03);'>
                <span style='font-size:12px;color:#6b7280;font-weight:600;'>{year}</span><br>
                <span style='font-size:22px;font-weight:800;color:{color};'>{sow*100:.1f}%</span>
                <span style='font-size:11px;color:#6b7280;'> SOW</span><br>
                <span style='font-size:11px;color:#374151;font-weight:600;'>
                Nova ${nova:,.0f} of ${total:,.0f} total
                </span></div>""",
                unsafe_allow_html=True,
            )
    else:
        st.info("SOW data unavailable")

# ── Row 3: Investigation report (full width) ──────────────────────────────────

st.markdown("---")

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
    rec_action   = inv.get("recommended_action", "monitor")
    confidence   = float(inv.get("confidence_score", inv.get("risk_score", 0)) or 0)
    narrative    = inv.get("score_explanation") or inv.get("summary_narrative") or ""
    rationale    = inv.get("action_rationale", "")
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
            narrative_clean = narrative.replace("$", "\\$").replace("_", " ")
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
