"""
streamlit_app/pages/2_Rep_HCP_Network.py — Rep→HCP relationship network graph.

Data source: GET /hcps (FastAPI only — no parquet reads).
State filter is client-side only (filters in memory after fetch).
"""

from __future__ import annotations

from collections import defaultdict

import streamlit as st
from streamlit_agraph import agraph, Node, Edge, Config

from components.api_client import APIError, get_client
from config import FLAG_LABELS, RISK_TIER_COLORS, TIER_ORDER

st.set_page_config(
    page_title="Rep–HCP Network",
    layout="wide",
    page_icon="🕸️",
)

# ── Session state defaults ─────────────────────────────────────────────────────

if "network_tier_filter" not in st.session_state:
    st.session_state["network_tier_filter"] = "Critical only"
if "network_state_filter" not in st.session_state:
    st.session_state["network_state_filter"] = "NY"
if "network_selected_hcp" not in st.session_state:
    st.session_state["network_selected_hcp"] = None
if "selected_hcp_id" not in st.session_state:
    st.session_state["selected_hcp_id"] = None

# ── Data fetching ──────────────────────────────────────────────────────────────

_TIER_FILTER_MAP = {
    "Critical only":     ["critical"],
    "Critical + High":   ["critical", "high"],
    "All tiers":         TIER_ORDER,
}


@st.cache_data(ttl=300)
def fetch_network_hcps(tier_filter_key: str) -> list[dict]:
    """Fetch up to 500 HCPs per tier for the network graph."""
    client = get_client()
    tiers = _TIER_FILTER_MAP[tier_filter_key]
    all_hcps: list[dict] = []
    per_tier_limit = 500
    for tier in tiers:
        data = client.get("/hcps", params={"tier": tier, "limit": per_tier_limit, "offset": 0})
        batch = data.get("hcps", [])
        all_hcps.extend(batch)
    return all_hcps


def filter_by_state(hcps: list[dict], state: str) -> list[dict]:
    if state == "All states":
        return hcps
    return [h for h in hcps if h.get("state") == state]


# ── Load data ─────────────────────────────────────────────────────────────────

with st.spinner("Loading HCP data…"):
    try:
        hcp_list = fetch_network_hcps(st.session_state["network_tier_filter"])
    except APIError as e:
        st.error(f"API error: {e}")
        st.stop()

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
    st.markdown("**LEGEND**")
    for tier, color in RISK_TIER_COLORS.items():
        st.markdown(
            f"<span style='color:{color};font-size:1.1em'>⬤</span> &nbsp;"
            f"{tier.capitalize()} HCP",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<span style='color:#6366f1;font-size:1.1em'>◆</span> &nbsp;Rep node",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<span style='color:#9CA3AF;font-size:0.85em'>Click a node to inspect it.</span>",
        unsafe_allow_html=True,
    )

# ── Page header ────────────────────────────────────────────────────────────────

hdr_left, hdr_right = st.columns([3, 1])
with hdr_left:
    st.markdown("## Rep–HCP Network")
    st.caption(
        "Default: critical HCPs only · Up to 500 HCPs per tier shown · "
        "Full population available in HCP Explorer"
    )

with hdr_right:
    st.markdown("")
    tier_choice = st.radio(
        "Tier filter",
        options=["Critical only", "Critical + High", "All tiers"],
        index=["Critical only", "Critical + High", "All tiers"].index(
            st.session_state["network_tier_filter"]
        ),
        horizontal=True,
        label_visibility="collapsed",
    )
    if tier_choice != st.session_state["network_tier_filter"]:
        st.session_state["network_tier_filter"] = tier_choice
        fetch_network_hcps.clear()
        st.rerun()

    all_states = sorted(set(
        h.get("state", "") for h in hcp_list if h.get("state")
    ))
    state_options = ["All states"] + all_states

    state_choice = st.selectbox(
        "State",
        options=state_options,
        index=state_options.index(
            st.session_state["network_state_filter"]
        ) if st.session_state["network_state_filter"] in state_options else 0,
        label_visibility="collapsed",
    )
    if state_choice != st.session_state["network_state_filter"]:
        st.session_state["network_state_filter"] = state_choice
        st.rerun()

# ── Apply state filter ─────────────────────────────────────────────────────────

filtered_hcps = filter_by_state(hcp_list, st.session_state["network_state_filter"])

# ── Build agraph network ───────────────────────────────────────────────────────

def _build_agraph(hcps: list[dict]):
    """Build streamlit-agraph nodes and edges from HCP list."""
    nodes = []
    edges = []  # Rep→HCP edges via primary_rep_id

    for hcp in hcps:
        hcp_id  = str(hcp.get("hcp_id", ""))
        score   = float(hcp.get("risk_score", 0))
        tier    = hcp.get("risk_tier", "low")
        color   = RISK_TIER_COLORS.get(tier, "#16A34A")
        label   = hcp_id.replace("HCP_", "")
        size    = 10 + (score / 100) * 20  # 10–30
        tooltip = f"Score: {score:.0f} | Tier: {tier.title()}"

        nodes.append(Node(
            id=hcp_id,
            label=label,
            size=size,
            color=color,
            title=tooltip,
        ))

    rep_ids = set(
        h["primary_rep_id"] for h in hcps
        if h.get("primary_rep_id")
    )

    for rep_id in rep_ids:
        nodes.append(Node(
            id=rep_id,
            label=rep_id,
            size=20,
            color="#6366f1",
            title=f"Rep: {rep_id}",
            shape="diamond",
        ))

    for hcp in hcps:
        if hcp.get("primary_rep_id"):
            edges.append(Edge(
                source=hcp["primary_rep_id"],
                target=hcp["hcp_id"],
                color="#d1d5db",
                width=1,
            ))

    return nodes, edges, len(rep_ids)


_AGRAPH_CONFIG = Config(
    width=1200,
    height=520,
    directed=False,
    physics=True,
    hierarchical=False,
    nodeHighlightBehavior=True,
    highlightColor="#FF0000",
    collapsible=False,
    node={"labelProperty": "label"},
    link={"labelProperty": "label", "renderLabel": False},
)

# ── Section A: Full-width graph ────────────────────────────────────────────────

if filtered_hcps:
    nodes, edges, n_reps = _build_agraph(filtered_hcps)
    clicked_id = agraph(nodes=nodes, edges=edges, config=_AGRAPH_CONFIG)

    if clicked_id:
        st.session_state["network_selected_hcp"] = clicked_id
        st.session_state["selected_hcp_id"] = clicked_id
else:
    st.info("No HCPs match the current filter.")
    n_reps = 0
    edges  = []

st.caption(
    f"Showing {n_reps} reps · {len(filtered_hcps)} HCPs · "
    f"{len(edges)} edges · "
    f"{st.session_state['network_state_filter']}"
)

# ── Build rep summary (used in both sections B columns) ───────────────────────

rep_summary: dict = defaultdict(lambda: {
    "hcp_count": 0,
    "critical": 0,
    "high": 0,
    "medium": 0,
    "low": 0,
    "total_risk": 0.0,
})
for hcp in filtered_hcps:
    rep = hcp.get("primary_rep_id")
    if not rep:
        continue
    tier = hcp.get("risk_tier", "low")
    rep_summary[rep]["hcp_count"] += 1
    rep_summary[rep][tier] += 1
    rep_summary[rep]["total_risk"] += float(hcp.get("risk_score", 0))

rep_rows = sorted(
    rep_summary.items(),
    key=lambda x: (-x[1]["critical"], -x[1]["high"], -x[1]["total_risk"]),
)

# ── Section B: Two columns below graph ────────────────────────────────────────

col_leaderboard, col_detail = st.columns([1.2, 1])

with col_leaderboard:
    st.markdown("#### Rep Risk Leaderboard")
    st.caption(
        f"{len(rep_rows)} reps · "
        f"{st.session_state['network_state_filter']} · "
        f"{st.session_state['network_tier_filter']}"
    )

    st.markdown(
        "<div style='display:grid;"
        "grid-template-columns:100px 70px 80px 80px 80px 80px;"
        "gap:8px;font-size:16px;font-weight:800;color:inherit;opacity:0.6;"
        "text-transform:uppercase;letter-spacing:0.05em;"
        "padding:8px 12px;border-bottom:2px solid rgba(255,255,255,0.15);"
        "margin-top:8px;'>"
        "<div>Rep ID</div>"
        "<div>HCPs</div>"
        "<div style='color:#DC2626;opacity:1;'>Critical</div>"
        "<div style='color:#EA580C;opacity:1;'>High</div>"
        "<div style='color:#CA8A04;opacity:1;'>Medium</div>"
        "<div>Avg HCP Risk Score</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    for i, (rep_id, stats) in enumerate(rep_rows[:15]):
        avg_risk = stats["total_risk"] / max(stats["hcp_count"], 1)
        row_bg = "rgba(255,255,255,0.05)" if i % 2 == 0 else "transparent"
        st.markdown(
            f"<div style='display:grid;"
            f"grid-template-columns:100px 70px 80px 80px 80px 80px;"
            f"gap:8px;font-size:16px;padding:10px 12px;"
            f"background:{row_bg};"
            f"border-bottom:1px solid rgba(255,255,255,0.08);"
            f"border-radius:4px;align-items:center;'>"
            f"<div style='font-weight:700;color:#60a5fa;font-size:15px;'>"
            f"{rep_id}</div>"
            f"<div style='font-weight:600;color:inherit;font-size:15px;'>"
            f"{stats['hcp_count']}</div>"
            f"<div style='font-weight:800;color:#DC2626;font-size:15px;'>"
            f"{stats['critical']}</div>"
            f"<div style='font-weight:800;color:#EA580C;font-size:15px;'>"
            f"{stats['high']}</div>"
            f"<div style='font-weight:600;color:#CA8A04;font-size:15px;'>"
            f"{stats['medium']}</div>"
            f"<div style='font-weight:700;color:inherit;font-size:15px;'>"
            f"{avg_risk:.0f}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

with col_detail:
    selected_id = st.session_state.get("network_selected_hcp")
    is_rep = selected_id and not selected_id.startswith("HCP_")

    st.markdown("---")

    if is_rep:
        # ── Rep node clicked ───────────────────────────────────────────────────
        st.markdown("""
<style>
[data-testid="stMetricLabel"] p {
    font-size: 16px !important;
    font-weight: 700 !important;
    color: inherit !important;
    opacity: 0.85;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
[data-testid="stMetricValue"] {
    font-size: 42px !important;
    font-weight: 800 !important;
}
</style>
""", unsafe_allow_html=True)
        rep_stats = rep_summary.get(selected_id, {})
        st.markdown(f"#### Rep: {selected_id}")
        st.metric("HCPs managed", rep_stats.get("hcp_count", 0))
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Critical HCPs", rep_stats.get("critical", 0))
            st.metric("High HCPs", rep_stats.get("high", 0))
        with col_b:
            st.metric("Medium HCPs", rep_stats.get("medium", 0))
            avg = rep_stats.get("total_risk", 0) / max(rep_stats.get("hcp_count", 1), 1)
            st.metric("Avg risk score", f"{avg:.0f}")
    else:
        # ── HCP node clicked (or nothing selected) ─────────────────────────────
        selected_hcp_dict: dict | None = None
        if selected_id:
            selected_hcp_dict = next(
                (h for h in filtered_hcps if str(h.get("hcp_id", "")) == selected_id),
                None,
            )

        st.markdown("#### Selected HCP")
        if selected_hcp_dict:
            hcp_id = str(selected_hcp_dict.get("hcp_id", "—"))
            score  = float(selected_hcp_dict.get("risk_score", 0))
            tier   = selected_hcp_dict.get("risk_tier", "low")
            color  = RISK_TIER_COLORS.get(tier, "#888")

            st.markdown(
                f"<div style='font-size:16px;font-weight:600;"
                f"color:inherit;margin-top:8px;'>"
                f"HCP ID: &nbsp;"
                f"<span style='color:#60a5fa;font-weight:700;"
                f"font-size:18px;'>{hcp_id}</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:18px;font-weight:700;"
                f"color:inherit;margin-top:8px;'>"
                f"Risk score: &nbsp;"
                f"<span style='font-size:22px;font-weight:800;'>"
                f"{score:.0f} / 100</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:18px;font-weight:700;"
                f"color:inherit;margin-top:8px;'>"
                f"Tier: &nbsp;"
                f"<span style='color:{color};"
                f"font-size:20px;font-weight:800;'>"
                f"{tier.capitalize()}</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
            if st.button(
                "Go to HCP Detail →",
                type="primary",
                use_container_width=True,
                key="net_goto_detail",
            ):
                st.session_state["selected_hcp_id"] = hcp_id
                st.switch_page("pages/4_HCP_Detail.py")
        else:
            st.markdown(
                "<span style='color:#9CA3AF'>Click a node in the graph "
                "to view details.</span>",
                unsafe_allow_html=True,
            )
