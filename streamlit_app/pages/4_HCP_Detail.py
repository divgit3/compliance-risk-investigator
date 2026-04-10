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
    st.caption("🔍 SHAP risk drivers available in investigation report below")

# ── Col 3: Peer benchmark ──────────────────────────────────────────────────────

with col_bench:
    st.markdown("#### Peer benchmark")
    try:
        bench      = fetch_hcp_benchmarks(hcp_id)
        percentile = float(bench.get("percentile_rank", 0))
        peer_avg   = float(bench.get("peer_avg_spend", bench.get("peer_avg_total_spend", 0)))
        peer_max   = float(bench.get("peer_max_spend", bench.get("peer_max_total_spend", 0)))
        hcp_spend  = float(bench.get("hcp_spend", bench.get("hcp_total_spend", 0)))

        if percentile > 0:
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
            col_a, col_b = st.columns(2)
            col_a.metric(
                "Nova spend vs peers", f"${hcp_spend:.2f}",
                help="Normalized spend score vs peer group",
            )
            col_b.metric(
                "Peer avg spend", f"${peer_avg:.2f}",
                help="Average normalized spend in peer group",
            )
            st.caption("Normalized scores · dollar amounts require Athena re-run")
        else:
            st.info("Percentile rank unavailable — Athena not reachable")
            st.caption("Real dollar peer benchmarks require Athena")

    except APIError:
        st.info("Benchmark data unavailable")

st.markdown("---")

# ── Row 2: Flags | ToV chart | SOW ────────────────────────────────────────────

spend = extract_tov(profile)

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
            for i, flag_name in enumerate(flags_list):
                if i < critical_flags:
                    bg_color = "#fef2f2"; border_color = "#DC2626"
                    text_color = "#991b1b"; severity_badge = "CRITICAL"; badge_bg = "#DC2626"
                elif i < critical_flags + high_flags:
                    bg_color = "#fff7ed"; border_color = "#EA580C"
                    text_color = "#9a3412"; severity_badge = "HIGH"; badge_bg = "#EA580C"
                else:
                    bg_color = "#fffbeb"; border_color = "#CA8A04"
                    text_color = "#854d0e"; severity_badge = "MEDIUM"; badge_bg = "#CA8A04"
                display = FLAG_LABELS.get(flag_name, flag_name.replace("_", " ").title())
                st.markdown(
                    f"""<div style='padding:6px 10px;margin-bottom:4px;border-radius:4px;
                    background:{bg_color};border-left:3px solid {border_color};'>
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
            other_all     = [max(0, spend[f"total_tov_{y}"] - spend[f"nova_tov_{y}"]) for y in ["2022","2023","2024"]]

            food       = [v for v, m in zip(food_all,       active_mask) if m]
            speaking   = [v for v, m in zip(speaking_all,   active_mask) if m]
            consulting = [v for v, m in zip(consulting_all, active_mask) if m]
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
                x=active_years, y=other,
                name="Other Companies", marker_color="rgba(148,163,184,0.6)",
                text=[f"${v:,.0f}" if v > 0 else "" for v in other],
                textposition="inside", textfont=dict(size=11, color="#374151", family="Arial Bold"),
            ))
            for yr, tot in zip(active_years, totals):
                if tot > 0:
                    fig_spend.add_annotation(
                        x=yr, y=tot,
                        text=f"<b>Total: ${tot:,.0f}</b>",
                        showarrow=False, yshift=10,
                        font=dict(size=11, color="#1e3a5f", family="Arial Black"),
                    )
            fig_spend.update_layout(
                barmode="stack",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                title=dict(text="", font=dict(size=1)),
                height=280,
                margin=dict(l=10, r=10, t=10, b=60),
                xaxis=dict(
                    tickmode="array", tickvals=active_years, ticktext=active_years,
                    tickfont=dict(size=12, color="#1e3a5f", family="Arial Black"),
                    showgrid=False,
                ),
                yaxis=dict(
                    tickprefix="$", tickfont=dict(size=10, color="#1e3a5f", family="Arial Bold"),
                    showgrid=False, zeroline=False,
                ),
                legend=dict(
                    font=dict(size=10, color="#1e3a5f", family="Arial Bold"),
                    bgcolor="rgba(255,255,255,0.8)", bordercolor="#1e3a5f", borderwidth=1,
                    orientation="h", x=0, y=-0.3,
                ),
                hoverlabel=dict(
                    bgcolor="#ffffff", bordercolor="#1e3a5f",
                    font=dict(size=12, color="#1e3a5f", family="Arial Bold"),
                ),
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
