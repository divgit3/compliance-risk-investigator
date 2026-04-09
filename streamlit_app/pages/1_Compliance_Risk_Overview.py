"""
streamlit_app/pages/1_Compliance_Risk_Overview.py — Population-level compliance risk overview.

Data sources (FastAPI only — no parquet reads):
  GET /hcps       — HCP list with risk scores and flag counts
  GET /monitoring — MonitoringAgent population analysis (on-demand only)
"""

from __future__ import annotations

from collections import defaultdict

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

if "filter_state" not in st.session_state:
    st.session_state["filter_state"] = []
if "filter_tier" not in st.session_state:
    st.session_state["filter_tier"] = []
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


def fetch_monitoring() -> dict:
    """Fetch monitoring report — NOT cached (agent endpoint)."""
    return get_client().get_agent("/monitoring")


# ── Load HCPs ─────────────────────────────────────────────────────────────────

with st.spinner("Loading population data…"):
    try:
        hcp_list, api_total = fetch_hcps_overview()
        critical_total = fetch_tier_total("critical")
        high_total     = fetch_tier_total("high")
        medium_total   = fetch_tier_total("medium")
        low_total      = fetch_tier_total("low")
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

    st.markdown("---")

    # Global filters section
    st.markdown("**GLOBAL FILTERS**")

    active_states: list[str] = st.session_state["filter_state"]
    active_tiers: list[str]  = st.session_state["filter_tier"]

    if not active_states and not active_tiers:
        st.markdown("_<span style='color:#888'>No filters active</span>_", unsafe_allow_html=True)
    else:
        # Render filter chips
        for s in list(active_states):
            col_chip, col_x = st.columns([5, 1])
            col_chip.markdown(
                f"<span style='background:#DBEAFE;padding:2px 8px;border-radius:12px;"
                f"font-size:0.85em'>State: {s}</span>",
                unsafe_allow_html=True,
            )
            if col_x.button("✕", key=f"rm_state_{s}", help=f"Remove {s}"):
                st.session_state["filter_state"] = [x for x in active_states if x != s]
                st.rerun()

        for t in list(active_tiers):
            color = RISK_TIER_COLORS.get(t, "#888")
            col_chip, col_x = st.columns([5, 1])
            col_chip.markdown(
                f"<span style='background:{color}22;border:1px solid {color};"
                f"padding:2px 8px;border-radius:12px;font-size:0.85em;"
                f"color:{color}'>Tier: {t} </span>",
                unsafe_allow_html=True,
            )
            if col_x.button("✕", key=f"rm_tier_{t}", help=f"Remove {t}"):
                st.session_state["filter_tier"] = [x for x in active_tiers if x != t]
                st.rerun()

        if st.button("Clear all filters", type="secondary"):
            st.session_state["filter_state"] = []
            st.session_state["filter_tier"] = []
            st.rerun()

    st.markdown("")

    # Add state filter
    all_states = sorted({h.get("state", "") for h in hcp_list if h.get("state")})
    if all_states:
        new_states = st.multiselect(
            "+ Add state filter",
            options=[s for s in all_states if s not in active_states],
            default=[],
            label_visibility="visible",
        )
        if new_states:
            st.session_state["filter_state"] = list(set(active_states + new_states))
            st.rerun()

    # Add tier filter
    new_tiers = st.multiselect(
        "+ Add tier filter",
        options=[t for t in TIER_ORDER if t not in active_tiers],
        default=[],
        label_visibility="visible",
    )
    if new_tiers:
        st.session_state["filter_tier"] = list(set(active_tiers + new_tiers))
        st.rerun()

# ── Apply global filters client-side ──────────────────────────────────────────

filtered = hcp_list
if st.session_state["filter_state"]:
    filtered = [h for h in filtered if h.get("state") in st.session_state["filter_state"]]
if st.session_state["filter_tier"]:
    filtered = [h for h in filtered if h.get("risk_tier") in st.session_state["filter_tier"]]

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

tier_counts: dict[str, int] = defaultdict(int)
for h in filtered:
    tier_counts[h.get("risk_tier", "low")] += 1

total = len(filtered)
any_flag_pct = (
    (critical_total + high_total + medium_total) / api_total * 100
    if api_total else 0.0
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total HCPs", f"{api_total:,}")
c2.metric("Critical", f"{critical_total:,}", delta=None)
c3.metric("High risk", f"{high_total:,}", delta=None)
c4.metric("Any-flag %", f"{any_flag_pct:.1f}%")

# Red/orange colour hint under critical/high metrics
st.markdown(
    "<style>.stMetric:nth-child(2) [data-testid='stMetricValue'] {color:#DC2626}"
    " .stMetric:nth-child(3) [data-testid='stMetricValue'] {color:#EA580C}</style>",
    unsafe_allow_html=True,
)

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
    tier_counts_api = {
        "Critical": critical_total,
        "High":     high_total,
        "Medium":   medium_total,
        "Low":      low_total,
    }
    bar_df = {
        "Tier":  list(tier_counts_api.keys()),
        "Count": list(tier_counts_api.values()),
    }
    fig_bar = px.bar(
        bar_df,
        x="Count",
        y="Tier",
        orientation="h",
        color="Tier",
        color_discrete_map={
            "Critical": RISK_TIER_COLORS["critical"],
            "High":     RISK_TIER_COLORS["high"],
            "Medium":   RISK_TIER_COLORS["medium"],
            "Low":      RISK_TIER_COLORS["low"],
        },
        title="Risk tier distribution",
        text="Count",
    )
    fig_bar.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig_bar.update_layout(
        paper_bgcolor="white",
        plot_bgcolor="white",
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, title=""),
        yaxis=dict(showgrid=False, zeroline=False, title="", autorange="reversed"),
        margin=dict(l=10, r=30, t=40, b=10),
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
            paper_bgcolor="white",
            plot_bgcolor="white",
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=False, zeroline=False),
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.info("Trend data unavailable — requires temporal field in HCP records")

st.markdown("---")

# ── Map / Specialty section ───────────────────────────────────────────────────

toggle = st.radio(
    "View by",
    options=["By state", "By specialty"],
    index=0 if st.session_state["overview_toggle"] == "By state" else 1,
    horizontal=True,
    label_visibility="collapsed",
)
st.session_state["overview_toggle"] = toggle

if toggle == "By state":
    # Aggregate counts by state from filtered HCPs
    state_agg: dict[str, dict[str, int]] = defaultdict(lambda: {t: 0 for t in TIER_ORDER})
    for h in filtered:
        s = h.get("state")
        if s:
            state_agg[s][h.get("risk_tier", "low")] += 1

    if state_agg:
        states      = list(state_agg.keys())
        high_crit   = [state_agg[s]["critical"] + state_agg[s]["high"] for s in states]
        critical    = [state_agg[s]["critical"] for s in states]
        high        = [state_agg[s]["high"] for s in states]
        medium      = [state_agg[s]["medium"] for s in states]
        low         = [state_agg[s]["low"] for s in states]

        fig_map = px.choropleth(
            locations=states,
            locationmode="USA-states",
            color=high_crit,
            scope="usa",
            color_continuous_scale=["#fca5a5", "#dc2626"],
            hover_name=states,
            hover_data={"critical": critical, "high": high, "medium": medium, "low": low},
            title="High-risk HCPs by state",
            labels={"color": "High+Critical"},
        )
        fig_map.update_layout(
            paper_bgcolor="white",
            geo=dict(bgcolor="white"),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_map, use_container_width=True)
    else:
        st.info("State data unavailable in dev — will populate after Athena fix")

else:  # By specialty
    spec_agg: dict[str, dict[str, int]] = defaultdict(lambda: {t: 0 for t in TIER_ORDER})
    for h in filtered:
        spec = h.get("specialty") or h.get("primary_specialty")
        if spec:
            spec_agg[spec][h.get("risk_tier", "low")] += 1

    if spec_agg:
        rows = []
        for spec, counts in spec_agg.items():
            for tier in TIER_ORDER:
                rows.append({"specialty": spec, "tier": tier.capitalize(), "count": counts[tier]})

        fig_spec = px.bar(
            rows,
            x="count",
            y="specialty",
            color="tier",
            orientation="h",
            barmode="stack",
            color_discrete_map={t.capitalize(): RISK_TIER_COLORS[t] for t in TIER_ORDER},
            title="Risk by specialty",
            labels={"count": "HCP count", "specialty": "Specialty", "tier": "Tier"},
        )
        fig_spec.update_layout(
            paper_bgcolor="white",
            plot_bgcolor="white",
            xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=False, zeroline=False),
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig_spec, use_container_width=True)
    else:
        st.info("Specialty data unavailable in dev — `specialty` field absent from HCP records")
