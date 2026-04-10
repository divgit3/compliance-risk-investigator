"""
streamlit_app/pages/2_Rep_HCP_Network.py — Rep→HCP relationship network graph.

Data source: GET /hcps (FastAPI only — no parquet reads).
Rep-HCP edges unavailable in dev — rep_id not in API response.
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

# ── Main layout ────────────────────────────────────────────────────────────────

col_net, col_detail = st.columns([2, 1])

# ── Build agraph network ───────────────────────────────────────────────────────

def _build_agraph(hcps: list[dict]):
    """Build streamlit-agraph nodes and edges from HCP list."""
    nodes = []
    edges = []  # no edges in dev — rep_id not available

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

    return nodes, edges


_AGRAPH_CONFIG = Config(
    width=900,
    height=500,
    directed=False,
    physics=True,
    hierarchical=False,
    nodeHighlightBehavior=True,
    highlightColor="#FF0000",
    collapsible=False,
    node={"labelProperty": "label"},
    link={"labelProperty": "label", "renderLabel": False},
)

with col_net:
    if hcp_list:
        nodes, edges = _build_agraph(hcp_list)
        clicked_id = agraph(nodes=nodes, edges=edges, config=_AGRAPH_CONFIG)

        if clicked_id:
            st.session_state["network_selected_hcp"] = clicked_id
            st.session_state["selected_hcp_id"] = clicked_id
    else:
        st.info("No HCPs match the current filter.")

    st.caption(
        f"Showing {len(hcp_list):,} HCPs · Up to 500 per tier for browser performance · "
        "Click a node to inspect · Rep edges coming after rep_id schema fix"
    )

    # ── Top 10 riskiest HCPs table ─────────────────────────────────────────────

    network_tier_filter = st.session_state.get("network_tier_filter", "Critical only")
    st.markdown(f"#### Top 10 Riskiest HCPs — {network_tier_filter}")

    top10 = sorted(hcp_list, key=lambda h: float(h.get("risk_score", 0)), reverse=True)[:10]

    if top10:
        header_cols = st.columns([1, 3, 2, 2])
        for col, label in zip(header_cols, ["Rank", "HCP ID", "Score", "Tier"]):
            col.markdown(f"**{label}**")

        for rank, hcp in enumerate(top10, 1):
            hcp_id     = str(hcp.get("hcp_id", "—"))
            score      = float(hcp.get("risk_score", 0))
            tier       = hcp.get("risk_tier", "low")
            tier_color = RISK_TIER_COLORS.get(tier, "#888")

            row_cols = st.columns([1, 3, 2, 2])
            row_cols[0].markdown(str(rank))
            if row_cols[1].button(hcp_id[-12:], key=f"top10_{hcp_id}_{rank}"):
                st.session_state["selected_hcp_id"] = hcp_id
                st.switch_page("pages/4_HCP_Detail.py")
            row_cols[2].markdown(f"{score:.0f}")
            row_cols[3].markdown(
                f"<span style='color:{tier_color};font-weight:600'>{tier.capitalize()}</span>",
                unsafe_allow_html=True,
            )
    else:
        st.info("No HCPs to display.")

# ── Right column: selected HCP + rep summary ──────────────────────────────────

with col_detail:
    selected_id = st.session_state.get("network_selected_hcp")
    selected_hcp_dict: dict | None = None
    if selected_id:
        selected_hcp_dict = next(
            (h for h in hcp_list if str(h.get("hcp_id", "")) == selected_id),
            None,
        )

    st.markdown("---")

    # Selected HCP panel
    st.markdown("#### Selected HCP")
    if selected_hcp_dict:
        hcp_id = str(selected_hcp_dict.get("hcp_id", "—"))
        score  = float(selected_hcp_dict.get("risk_score", 0))
        tier   = selected_hcp_dict.get("risk_tier", "low")
        color  = RISK_TIER_COLORS.get(tier, "#888")

        st.markdown(f"**HCP ID:** `{hcp_id}`")
        st.markdown(f"**Risk score:** {score:.0f} / 100")
        st.markdown(
            f"**Tier:** <span style='color:{color};font-weight:600'>"
            f"{tier.capitalize()}</span>",
            unsafe_allow_html=True,
        )

        if st.button("Go to HCP Detail →", type="primary", use_container_width=True):
            st.session_state["selected_hcp_id"] = hcp_id
            st.switch_page("pages/4_HCP_Detail.py")
    else:
        st.markdown(
            "<span style='color:#9CA3AF'>Click a node in the graph "
            "to view details.</span>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # Rep summary panel
    st.markdown("#### Rep portfolio")
    st.info(
        "Rep data unavailable — rep_id not in API response. "
        "This panel will populate after the schema fix."
    )
