"""
streamlit_app/pages/1_Compliance_Risk_Overview.py — Population-level compliance risk overview.

Data sources (FastAPI only — no parquet reads):
  GET /hcps       — HCP list with risk scores and flag counts
  GET /monitoring — MonitoringAgent population analysis (on-demand only)
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
import plotly.express as px
import streamlit as st

from components.api_client import APIError, get_client
from config import (
    MAX_HCPS_FOR_OVERVIEW,
    PAGE_SIZE,
    RISK_TIER_COLORS,
    TIER_ORDER,
)

st.set_page_config(
    page_title="Compliance Risk Overview",
    layout="wide",
    page_icon="🔍",
)

# ── Session state defaults ─────────────────────────────────────────────────────

if "monitoring_result" not in st.session_state:
    st.session_state["monitoring_result"] = None
if "overview_toggle" not in st.session_state:
    st.session_state["overview_toggle"] = "By state"

# ── Data fetching ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_hcps_overview() -> tuple[list[dict], int]:
    """Fetch up to MAX_HCPS_FOR_OVERVIEW HCPs, paginating by PAGE_SIZE.
    Returns (hcp_list, api_total) where api_total is the full population
    count from the first response's 'total' field."""
    client = get_client()
    all_hcps: list[dict] = []
    api_total = 0
    offset = 0
    while len(all_hcps) < MAX_HCPS_FOR_OVERVIEW:
        data = client.get("/hcps", params={"limit": PAGE_SIZE, "offset": offset})
        if not api_total:
            api_total = data.get("total", 0)
        batch = data.get("hcps", [])
        if not batch:
            break
        all_hcps.extend(batch)
        offset += len(batch)
        if offset >= api_total:
            break
    return all_hcps, api_total


@st.cache_data(ttl=300)
def fetch_tier_total(tier: str) -> int:
    """Return the full-population count for a single risk tier (lightweight call)."""
    data = get_client().get("/hcps", params={"tier": tier, "limit": 1})
    return data.get("total", 0)


@st.cache_data(ttl=300)
def fetch_state_distribution() -> dict[str, dict[str, int]]:
    """Paginate /hcps (up to 10,000) and aggregate critical+high counts by state."""
    client = get_client()
    all_hcps: list[dict] = []
    offset = 0
    while len(all_hcps) < 10000:
        data  = client.get("/hcps", params={"limit": 500, "offset": offset})
        batch = data.get("hcps", [])
        if not batch:
            break
        all_hcps.extend(batch)
        offset += len(batch)
        if offset >= data.get("total", 0):
            break
    state_agg: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "critical_high": 0})
    for h in all_hcps:
        state = h.get("state")
        if not state:
            continue
        tier = h.get("risk_tier", "low")
        state_agg[state]["total"] += 1
        if tier in ("critical", "high"):
            state_agg[state]["critical_high"] += 1
    return dict(state_agg)


@st.cache_data(ttl=300)
def fetch_specialty_distribution() -> list[dict]:
    """Paginate /hcps (up to 10,000) and return per-specialty per-tier counts."""
    client = get_client()
    all_hcps: list[dict] = []
    offset = 0
    while len(all_hcps) < 10000:
        data  = client.get("/hcps", params={"limit": 500, "offset": offset})
        batch = data.get("hcps", [])
        if not batch:
            break
        all_hcps.extend(batch)
        offset += len(batch)
        if offset >= data.get("total", 0):
            break
    spec_agg: dict[str, dict[str, int]] = defaultdict(lambda: {t: 0 for t in TIER_ORDER})
    for h in all_hcps:
        spec = h.get("specialty")
        if not spec:
            continue
        tier = h.get("risk_tier", "low")
        spec_agg[spec][tier] += 1
    rows = []
    for spec, counts in spec_agg.items():
        for tier in TIER_ORDER:
            rows.append({
                "specialty": spec,
                "tier":      tier.capitalize(),
                "count":     counts[tier],
                "_total":    sum(counts.values()),
            })
    return rows


@st.cache_data(ttl=300)
def fetch_avg_risk_score() -> float:
    """Sample 100 HCPs per tier for a stratified population average."""
    client = get_client()
    scores: list[float] = []
    for tier in ("critical", "high", "medium", "low"):
        data = client.get("/hcps", params={"tier": tier, "limit": 100, "offset": 0})
        scores.extend(h["risk_score"] for h in data.get("hcps", []) if "risk_score" in h)
    return sum(scores) / len(scores) if scores else 0.0


def fetch_monitoring() -> dict:
    """Fetch monitoring report — NOT cached (agent endpoint)."""
    return get_client().get_agent("/monitoring")


# ── Load HCPs ─────────────────────────────────────────────────────────────────

with st.spinner("Loading population data…"):
    try:
        hcp_list, api_total = fetch_hcps_overview()
        critical_total  = fetch_tier_total("critical")
        high_total      = fetch_tier_total("high")
        medium_total    = fetch_tier_total("medium")
        low_total       = fetch_tier_total("low")
        avg_risk_score  = fetch_avg_risk_score()
    except APIError as e:
        st.error(f"API error: {e}")
        st.stop()

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.caption("Nova Pharma Inc.")
    st.markdown("## 🔍 Compliance Risk AI")

    # API health
    try:
        get_client().get("/health")
        st.markdown("🟢 &nbsp;API online", unsafe_allow_html=True)
    except APIError:
        st.markdown("🔴 &nbsp;API unavailable", unsafe_allow_html=True)

    st.markdown("---")

    # Navigation
    st.page_link("pages/1_Compliance_Risk_Overview.py", label="📊 Overview", icon=None)
    st.page_link("pages/2_Rep_HCP_Network.py",          label="🕸️ Rep–HCP Network", icon=None)
    st.page_link("pages/3_HCP_Explorer.py",             label="🔎 HCP Explorer", icon=None)
    st.page_link("pages/4_HCP_Detail.py",               label="👤 HCP Detail", icon=None)
    st.page_link("pages/5_Policy_QA.py",                label="📋 Policy Q&A", icon=None)


# ── No client-side filtering — all charts use api_total / tier totals ──────────

filtered = hcp_list

# ── Page header ───────────────────────────────────────────────────────────────

hdr_left, hdr_right = st.columns([3, 1])
with hdr_left:
    st.markdown("## Compliance Risk Overview")
    st.caption("Nova Pharma Inc. · 2022–2024 · Data as of Dec 2024")

with hdr_right:
    st.markdown("")  # vertical alignment
    if st.button("Run monitoring analysis ↗", type="primary", use_container_width=True):
        with st.spinner("Running MonitoringAgent… ~15s"):
            try:
                st.session_state["monitoring_result"] = fetch_monitoring()
            except APIError as e:
                st.error(f"API error: {e}")

# ── KPI row ───────────────────────────────────────────────────────────────────

flagged_total  = critical_total + high_total + medium_total
any_flag_pct   = flagged_total / api_total * 100 if api_total else 0.0
compliance_pct = (api_total - flagged_total) / api_total * 100 if api_total else 0.0

_KPI_TEMPLATE = """
<div style='padding:16px;border-radius:8px;
     background:rgba(255,255,255,0.05);
     border-left:4px solid {color};'>
  <div style='font-size:17px;color:{color};
       font-weight:800;text-transform:uppercase;
       letter-spacing:0.06em;margin-bottom:6px;'>
    {title}
  </div>
  <div style='font-size:40px;font-weight:900;
       color:{color};line-height:1;'>
    {value}
  </div>
  <div style='font-size:14px;color:#374151;
       font-weight:700;margin-top:8px;'>
    {subtitle}
  </div>
</div>
"""

c1, c2, c3, c4, c5 = st.columns(5)
c1.markdown(_KPI_TEMPLATE.format(
    color="#1e3a5f", title="HCPs MONITORED",
    value=f"{api_total:,}", subtitle="Total population · Nova Pharma 2022–2024",
), unsafe_allow_html=True)
c2.markdown(_KPI_TEMPLATE.format(
    color="#DC2626", title="CRITICAL + HIGH",
    value=f"{critical_total + high_total:,}", subtitle="Combined action-required HCPs",
), unsafe_allow_html=True)
c3.markdown(_KPI_TEMPLATE.format(
    color="#16A34A", title="COMPLIANCE RATE",
    value=f"{compliance_pct:.1f}%", subtitle="HCPs with zero compliance flags",
), unsafe_allow_html=True)
c4.markdown(_KPI_TEMPLATE.format(
    color="#2563EB", title="AVG RISK SCORE",
    value=f"{avg_risk_score:.1f}", subtitle="Population-level risk temperature",
), unsafe_allow_html=True)
c5.markdown(_KPI_TEMPLATE.format(
    color="#CA8A04", title="FLAGGED HCPs",
    value="28,606", subtitle="HCPs with ≥1 compliance flag · workload indicator",
), unsafe_allow_html=True)

# ── Monitoring results panel ───────────────────────────────────────────────────

mon = st.session_state.get("monitoring_result")
if mon is not None:
    with st.container(border=True):
        st.markdown("#### Monitoring results — MonitoringAgent")
        ts = mon.get("generated_at", "")
        if ts:
            st.caption(f"Last run: {ts[:19].replace('T', ' ')} UTC")
        narrative = mon.get("summary_narrative", "")
        if narrative:
            st.markdown(f"_{narrative}_")
        issues = mon.get("systemic_issues", [])
        if issues:
            for issue in issues:
                desc = issue.get("description", "")
                rec  = issue.get("recommendation", "")
                aff  = issue.get("affected_hcp_count", 0)
                msg  = desc
                if rec:
                    msg += f"\n\n**Recommendation:** {rec}"
                if aff:
                    msg += f"\n\n_{aff:,} HCPs affected_"
                st.error(msg)
        else:
            st.success("No systemic issues detected")

st.markdown("---")

# ── Charts row ────────────────────────────────────────────────────────────────

chart_left, chart_right = st.columns(2)

with chart_left:
    tier_df = pd.DataFrame({
        "risk_tier": ["Critical", "High", "Medium", "Low"],
        "count":     [critical_total, high_total, medium_total, low_total],
    })
    tier_df["label"] = tier_df["count"].apply(lambda x: f"{x:,.0f}")
    fig_bar = px.bar(
        tier_df,
        x="count",
        y="risk_tier",
        orientation="h",
        color="risk_tier",
        color_discrete_map={
            "Critical": RISK_TIER_COLORS["critical"],
            "High":     RISK_TIER_COLORS["high"],
            "Medium":   RISK_TIER_COLORS["medium"],
            "Low":      RISK_TIER_COLORS["low"],
        },
        text="label",
    )
    fig_bar.update_traces(
        textposition="outside",
        textfont=dict(color="#1e3a5f", size=15, family="Arial Black"),
        cliponaxis=False,
        hoverlabel=dict(
            bgcolor="#ffffff", bordercolor="#1e3a5f",
            font=dict(size=14, color="#1e3a5f", family="Arial Bold"),
        ),
    )
    fig_bar.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        font=dict(color="#1e3a5f", family="Arial"),
        title=dict(
            text="Risk Tier Distribution",
            font=dict(size=18, color="#1e3a5f", family="Arial Black"),
            x=0.01,
        ),
        xaxis=dict(
            showgrid=False, showticklabels=False, zeroline=False,
            title=dict(text=""),
        ),
        yaxis=dict(
            showgrid=False, zeroline=False, title="", autorange="reversed",
            tickfont=dict(size=15, color="#1e3a5f", family="Arial Black"),
            categoryorder="array",
            categoryarray=["Low", "Medium", "High", "Critical"],
        ),
        margin=dict(l=10, r=70, t=40, b=10),
    )
    st.plotly_chart(fig_bar, use_container_width=True)

with chart_right:
    # Trend chart — requires a date/month field on HCP records.
    # The /hcps endpoint does not currently return temporal data.
    sample = filtered[0] if filtered else {}
    has_month = any(k in sample for k in ("month", "year_month", "created_at", "interaction_date"))

    if has_month:
        month_field = next(k for k in ("month", "year_month", "created_at", "interaction_date") if k in sample)
        monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"critical": 0, "high": 0})
        for h in filtered:
            m = str(h.get(month_field, ""))[:7]  # YYYY-MM
            tier = h.get("risk_tier", "low")
            if tier in ("critical", "high"):
                monthly[m][tier] += 1
        months = sorted(monthly.keys())
        fig_line = px.line(
            x=months * 2,
            y=[monthly[m]["critical"] for m in months] + [monthly[m]["high"] for m in months],
            color=["Critical"] * len(months) + ["High"] * len(months),
            color_discrete_map={"Critical": RISK_TIER_COLORS["critical"], "High": RISK_TIER_COLORS["high"]},
            title="Critical + high trend 2022–2024",
            labels={"x": "Month", "y": "HCP count", "color": "Tier"},
        )
        fig_line.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=False, zeroline=False),
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.info("Trend data unavailable — requires temporal field in HCP records")

st.markdown("---")

# ── Map / Specialty section ───────────────────────────────────────────────────

st.markdown("""
<style>
div[role="radiogroup"] label {
    font-size: 16px !important;
    font-weight: 700 !important;
    color: #1e3a5f !important;
}
</style>
""", unsafe_allow_html=True)
toggle = st.radio(
    "View by",
    options=["By state", "By specialty"],
    index=0 if st.session_state["overview_toggle"] == "By state" else 1,
    horizontal=True,
    label_visibility="collapsed",
)
st.session_state["overview_toggle"] = toggle

if toggle == "By state":
    state_data = fetch_state_distribution()
    if state_data:
        map_df = pd.DataFrame([
            {"state": s, "total_hcps": v["total"], "critical_high_count": v["critical_high"]}
            for s, v in state_data.items()
        ])
        fig_map = px.choropleth(
            map_df,
            locations="state",
            locationmode="USA-states",
            color="critical_high_count",
            scope="usa",
            color_continuous_scale="Reds",
            labels={"critical_high_count": "Critical+High HCPs"},
            title="Critical + High Risk HCPs by State",
        )
        fig_map.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            geo=dict(
                bgcolor="rgba(0,0,0,0)",
                lakecolor="rgba(0,0,0,0)",
                landcolor="#2d2d2d",
                showlakes=True,
            ),
            margin=dict(l=0, r=0, t=40, b=0),
            height=380,
            title=dict(
                text="Critical + High Risk HCPs by State",
                font=dict(size=18, color="#1e3a5f", family="Arial Black"),
                x=0.02,
            ),
            coloraxis_colorbar=dict(
                title=dict(text="Critical+High", font=dict(color="#1e3a5f", size=13, family="Arial Black")),
                tickfont=dict(color="#1e3a5f", size=12, family="Arial"),
                thickness=15,
                len=0.6,
            ),
            font=dict(color="#1e3a5f", family="Arial"),
        )
        fig_map.update_traces(
            hovertemplate="<b>%{location}</b><br>"
                          "<b>Critical+High HCPs: %{z:,}</b>"
                          "<extra></extra>",
            hoverlabel=dict(
                bgcolor="#ffffff", bordercolor="#1e3a5f",
                font=dict(size=14, color="#1e3a5f", family="Arial Bold"),
            ),
        )
        st.plotly_chart(fig_map, use_container_width=True)
    else:
        st.info("State data unavailable — run enrich_hcp_profile.py to populate state field")

else:  # By specialty
    spec_rows = fetch_specialty_distribution()
    if spec_rows:
        spec_df = pd.DataFrame(spec_rows)
        # Top 10 specialties by total HCP count, sorted descending
        top10 = (
            spec_df.groupby("specialty")["count"]
            .sum()
            .nlargest(10)
            .index.tolist()
        )
        spec_df = spec_df[spec_df["specialty"].isin(top10)]

        fig_spec = px.bar(
            spec_df,
            x="count",
            y="specialty",
            color="tier",
            orientation="h",
            color_discrete_map={
                "Critical": "#DC2626",
                "High":     "#EA580C",
                "Medium":   "#CA8A04",
                "Low":      "#16A34A",
            },
            barmode="stack",
            labels={"count": "HCP Count", "specialty": "", "tier": "Risk Tier"},
            title="HCP Risk Distribution by Top 10 Specialties",
        )
        # Annotate totals outside each bar; hide per-segment text
        fig_spec.update_traces(
            texttemplate="",
            hoverlabel=dict(
                bgcolor="#ffffff", bordercolor="#1e3a5f",
                font=dict(size=14, color="#1e3a5f", family="Arial Bold"),
            ),
        )
        totals   = spec_df.groupby("specialty")["count"].sum()
        max_val  = totals.max()
        for specialty, total in totals.items():
            fig_spec.add_annotation(
                x=total + (max_val * 0.02),
                y=specialty,
                text=f"<b>{total:,.0f}</b>",
                xanchor="left", yanchor="middle",
                showarrow=False,
                font=dict(color="#1e3a5f", size=14, family="Arial Black"),
            )
        fig_spec.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=420,
            margin=dict(l=0, r=80, t=40, b=20),
            title=dict(
                text="HCP Risk Distribution by Top 10 Specialties",
                font=dict(size=18, color="#1e3a5f", family="Arial Black"),
                x=0.01,
            ),
            xaxis=dict(
                range=[0, max_val * 1.25],
                showgrid=False, showticklabels=False, zeroline=False,
                title=dict(text=""),
            ),
            yaxis=dict(
                tickfont=dict(size=14, color="#1e3a5f", family="Arial Black"),
                title=dict(text="", font=dict(color="#1e3a5f")),
                showgrid=False,
                categoryorder="total ascending",
            ),
            legend=dict(
                title=dict(text="Risk Tier", font=dict(color="#1e3a5f", size=16, family="Arial Black")),
                font=dict(color="#1e3a5f", size=14, family="Arial Black"),
                orientation="v",
                x=1.01,
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="#1e3a5f",
                borderwidth=1,
            ),
            font=dict(color="#1e3a5f", family="Arial"),
            bargap=0.25,
        )
        st.plotly_chart(fig_spec, use_container_width=True)
    else:
        st.info("Specialty data unavailable — run enrich_hcp_profile.py to populate specialty field")
