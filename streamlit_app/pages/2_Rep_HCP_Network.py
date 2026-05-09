# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
streamlit_app/pages/2_Rep_HCP_Network.py — Rep→HCP relationship network graph.

Data source: GET /hcps + GET /hcps/rep-edges (FastAPI only — no parquet reads).
State filter for HCP nodes is client-side; edges are fetched pre-filtered by state.
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


@st.cache_data(ttl=300)
def fetch_network_edges(tier_filter_key: str, state: str) -> list[dict]:
    """Fetch rep-HCP edges matching the current filter set."""
    client = get_client()
    tiers = _TIER_FILTER_MAP[tier_filter_key]
    params: dict = {"tier": tiers, "limit": 500}
    if state != "All states":
        params["state"] = state
    data = client.get("/hcps/rep-edges", params=params)
    return data.get("edges", [])


def filter_by_state(hcps: list[dict], state: str) -> list[dict]:
    if state == "All states":
        return hcps
    return [h for h in hcps if h.get("state") == state]


# ── Load data ─────────────────────────────────────────────────────────────────

with st.spinner("Loading network data…"):
    try:
        hcp_list  = fetch_network_hcps(st.session_state["network_tier_filter"])
        edge_list = fetch_network_edges(
            st.session_state["network_tier_filter"],
            st.session_state["network_state_filter"],
        )
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

st.markdown("## Rep–HCP Network")
st.caption(
    "Default: critical HCPs only · Up to 500 HCPs per tier · "
    "Full population available in HCP Explorer"
)

# ── State options (computed before columns, needed by side panel dropdown) ─────

all_states = sorted(set(
    h.get("state", "") for h in hcp_list if h.get("state")
))
state_options = ["All states"] + all_states

# ── Apply state filter ─────────────────────────────────────────────────────────

filtered_hcps = filter_by_state(hcp_list, st.session_state["network_state_filter"])

# ── Build agraph network ───────────────────────────────────────────────────────

def _build_agraph(
    hcps: list[dict],
    edge_rows: list[dict],
    selected_id: str | None = None,
):
    """Build streamlit-agraph nodes and edges, with optional 2-hop focus.

    Returns (nodes, edges, n_reps, hop_info). hop_info is None when nothing
    is selected; otherwise a dict with selected_id, is_rep, n_hop1, n_hop2.
    """
    _DIM_NODE = "#f3f4f6"
    _DIM_EDGE = "#f9fafb"

    def _edge_hop_level(e, hop0, hop1, hop2, focused):
        if not (focused and e["rep_id"] in focused and e["hcp_id"] in focused):
            return None  # outside focus — dim
        if e["rep_id"] in hop0 or e["hcp_id"] in hop0:
            return 0
        if e["rep_id"] in hop1 or e["hcp_id"] in hop1:
            return 1
        return 2

    # Edges only to HCPs actually rendered (defensive against stale caches)
    rendered_hcp_ids = {str(h.get("hcp_id", "")) for h in hcps}
    valid_edges = [e for e in edge_rows if e.get("hcp_id") in rendered_hcp_ids]

    rep_ids = {e["rep_id"] for e in valid_edges if e.get("rep_id")}

    # Compute 2-hop focused set if a valid node is selected
    focused_nodes: set[str] | None = None
    hop0: set[str] = set()
    hop1: set[str] = set()
    hop2: set[str] = set()
    hop_info: dict | None = None
    all_node_ids = rendered_hcp_ids | rep_ids

    if selected_id and selected_id in all_node_ids:
        neighbors: dict[str, set[str]] = defaultdict(set)
        for e in valid_edges:
            r, h = e["rep_id"], e["hcp_id"]
            neighbors[r].add(h)
            neighbors[h].add(r)
        hop0 = {selected_id}
        hop1 = set().union(*(neighbors[n] for n in hop0)) - hop0
        hop2 = set().union(*(neighbors[n] for n in hop1)) - hop0 - hop1
        focused_nodes = hop0 | hop1 | hop2
        hop_info = {
            "selected_id": selected_id,
            "is_rep": not selected_id.startswith("HCP_"),
            "n_hop1": len(hop1),
            "n_hop2": len(hop2),
        }

    nodes = []

    for hcp in hcps:
        hcp_id  = str(hcp.get("hcp_id", ""))
        score   = float(hcp.get("risk_score", 0))
        tier    = hcp.get("risk_tier", "low")
        color   = RISK_TIER_COLORS.get(tier, "#16A34A")
        label   = hcp_id.replace("HCP_", "")
        size    = 10 + (score / 100) * 20  # 10–30
        tooltip = f"Score: {score:.0f} | Tier: {tier.title()}"

        if hcp_id == selected_id:
            size  = size * 2.0
            color = "#fbbf24"
            label = label + " ★"
        elif focused_nodes is not None and hcp_id not in focused_nodes:
            color = _DIM_NODE

        nodes.append(Node(
            id=hcp_id, label=label, size=size, color=color, title=tooltip,
        ))

    for rep_id in rep_ids:
        rep_color = "#6366f1"
        rep_size  = 20
        rep_label = rep_id
        if rep_id == selected_id:
            rep_size  = 40
            rep_color = "#fbbf24"
            rep_label = rep_id + " ★"
        elif focused_nodes is not None and rep_id not in focused_nodes:
            rep_color = _DIM_NODE
        nodes.append(Node(
            id=rep_id, label=rep_label, size=rep_size, color=rep_color,
            title=f"Rep: {rep_id}", shape="diamond",
        ))

    edges = []
    for e in valid_edges:
        is_primary = e.get("is_primary", False)
        n_inter    = e.get("interaction_count", 1)
        if focused_nodes is None:
            edge_color = "#9ca3af" if is_primary else "#e5e7eb"
            edge_width = 2 if is_primary else 0.8
        else:
            hop = _edge_hop_level(e, hop0, hop1, hop2, focused_nodes)
            if hop is None:
                edge_color, edge_width = _DIM_EDGE, 0.3
            elif hop == 0:
                edge_color, edge_width = "#374151", 2.5
            elif hop == 1:
                edge_color, edge_width = "#9ca3af", 1.5
            else:
                edge_color, edge_width = "#d1d5db", 1.0
        edges.append(Edge(
            source=e["rep_id"],
            target=e["hcp_id"],
            color=edge_color,
            width=edge_width,
            title=f"{n_inter} interactions" + (" (primary)" if is_primary else ""),
        ))

    return nodes, edges, len(rep_ids), hop_info


_AGRAPH_CONFIG = Config(
    width=900,
    height=780,
    directed=False,
    physics=True,
    hierarchical=False,
    nodeHighlightBehavior=True,
    highlightColor="#FF0000",
    collapsible=False,
    node={"labelProperty": "label"},
    link={"labelProperty": "label", "renderLabel": False},
)

# ── Build graph data (before columns; agraph render happens inside col_main) ───

nodes, edges, n_reps, hop_info = _build_agraph(
    filtered_hcps,
    edge_list,
    st.session_state.get("network_selected_hcp"),
)

# ── Build rep summary (primary-rep only; used by leaderboard + detail pane) ───

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

# ── Main layout: [graph + detail | filters + leaderboard] ─────────────────────

state_label = st.session_state["network_state_filter"]

col_main, col_side = st.columns([0.65, 0.35])

with col_side:
    tier_choice = st.radio(
        "Tier filter",
        options=["Critical only", "Critical + High", "All tiers"],
        index=["Critical only", "Critical + High", "All tiers"].index(
            st.session_state["network_tier_filter"]
        ),
        horizontal=True,
    )
    if tier_choice != st.session_state["network_tier_filter"]:
        st.session_state["network_tier_filter"] = tier_choice
        fetch_network_hcps.clear()
        fetch_network_edges.clear()
        st.rerun()

    state_choice = st.selectbox(
        "State",
        options=state_options,
        index=state_options.index(
            st.session_state["network_state_filter"]
        ) if st.session_state["network_state_filter"] in state_options else 0,
    )
    if state_choice != st.session_state["network_state_filter"]:
        st.session_state["network_state_filter"] = state_choice
        st.rerun()

    # ── Selected detail panel ──────────────────────────────────────────────────
    selected_id     = st.session_state.get("network_selected_hcp")
    is_rep_selected = selected_id and not selected_id.startswith("HCP_")

    PANEL_STYLE = (
        "padding:14px 16px;"
        "background:rgba(96,165,250,0.08);"
        "border-left:3px solid #60a5fa;"
        "border-radius:4px;margin:8px 0;"
        "min-height:220px;"
        "box-sizing:border-box;"
    )

    if is_rep_selected:
        # Compute from edge_list, not rep_summary — rep_summary is primary-only
        # but the graph shows all connections; panel should match the graph.
        rep_edges        = [e for e in edge_list if e["rep_id"] == selected_id]
        connected_hcp_ids = {e["hcp_id"] for e in rep_edges}
        connected_hcps   = [
            h for h in filtered_hcps
            if str(h.get("hcp_id", "")) in connected_hcp_ids
        ]
        hcps        = len(connected_hcps)
        crit        = sum(1 for h in connected_hcps if h.get("risk_tier") == "critical")
        high        = sum(1 for h in connected_hcps if h.get("risk_tier") == "high")
        med         = sum(1 for h in connected_hcps if h.get("risk_tier") == "medium")
        n_primary   = sum(1 for e in rep_edges if e.get("is_primary"))
        n_secondary = len(rep_edges) - n_primary
        total_risk  = sum(float(h.get("risk_score", 0)) for h in connected_hcps)
        avg         = total_risk / max(hcps, 1)
        panel_html = (
            f"<div style='{PANEL_STYLE}'>"
            f"<div style='font-size:13px;font-weight:700;"
            f"text-transform:uppercase;letter-spacing:0.05em;"
            f"opacity:0.75;margin-bottom:6px;'>Rep</div>"
            f"<div style='font-size:20px;font-weight:800;"
            f"color:#60a5fa;margin-bottom:14px;'>{selected_id}</div>"
            f"<div style='font-size:14px;line-height:1.9;'>"
            f"<div><strong>{hcps}</strong> HCPs connected "
            f"<span style='opacity:0.7;font-size:13px;'>"
            f"({n_primary} primary · {n_secondary} secondary)"
            f"</span></div>"
            f"<div style='margin-top:4px;'>"
            f"<span style='color:#DC2626;font-weight:700;'>{crit} Critical</span>"
            f" &nbsp;·&nbsp; "
            f"<span style='color:#EA580C;font-weight:700;'>{high} High</span>"
            f" &nbsp;·&nbsp; "
            f"<span style='color:#CA8A04;font-weight:700;'>{med} Medium</span>"
            f"</div>"
            f"<div style='margin-top:4px;'>"
            f"Avg risk score: <strong>{avg:.0f}</strong>"
            f"</div>"
            f"</div></div>"
        )
        st.markdown(panel_html, unsafe_allow_html=True)
    else:
        selected_hcp_dict: dict | None = None
        if selected_id:
            selected_hcp_dict = next(
                (h for h in filtered_hcps if str(h.get("hcp_id", "")) == selected_id),
                None,
            )
        if selected_hcp_dict:
            hcp_id = str(selected_hcp_dict.get("hcp_id", "—"))
            score  = float(selected_hcp_dict.get("risk_score", 0))
            tier   = selected_hcp_dict.get("risk_tier", "low")
            color  = RISK_TIER_COLORS.get(tier, "#888")
            panel_html = (
                f"<div style='{PANEL_STYLE}'>"
                f"<div style='font-size:13px;font-weight:700;"
                f"text-transform:uppercase;letter-spacing:0.05em;"
                f"opacity:0.75;margin-bottom:6px;'>Selected HCP</div>"
                f"<div style='font-size:20px;font-weight:800;"
                f"color:#60a5fa;margin-bottom:14px;'>{hcp_id}</div>"
                f"<div style='font-size:14px;line-height:1.9;'>"
                f"<div>Risk score: "
                f"<strong>{score:.0f} / 100</strong></div>"
                f"<div>Tier: "
                f"<strong style='color:{color};'>{tier.capitalize()}</strong></div>"
                f"</div></div>"
            )
            st.markdown(panel_html, unsafe_allow_html=True)
            if st.button(
                "Go to HCP Detail →",
                type="primary",
                use_container_width=True,
                key="net_goto_detail",
            ):
                st.session_state["selected_hcp_id"] = hcp_id
                st.switch_page("pages/4_HCP_Detail.py")
        else:
            placeholder_style = (
                "padding:14px 16px;color:#9CA3AF;font-size:14px;"
                "border-top:1px solid rgba(255,255,255,0.08);"
                "border-bottom:1px solid rgba(255,255,255,0.08);"
                "margin:12px 0;min-height:220px;box-sizing:border-box;"
                "display:flex;align-items:center;justify-content:center;"
                "text-align:center;"
            )
            st.markdown(
                "<div style='" + placeholder_style + "'>"
                "No node selected · Click a node in the graph for details"
                "</div>",
                unsafe_allow_html=True,
            )

    # ── Rep Risk Leaderboard ───────────────────────────────────────────────────
    st.markdown(f"#### Rep Risk Leaderboard — {state_label}")
    st.caption(
        f"Primary reps only · {len(rep_rows)} reps · "
        f"{st.session_state['network_tier_filter']}"
    )

    _GRID = "display:grid;grid-template-columns:100px 70px 80px 80px 80px 80px;gap:8px;"
    _HDR  = ("font-size:15px;font-weight:800;color:inherit;opacity:0.6;"
             "text-transform:uppercase;letter-spacing:0.05em;"
             "padding:8px 12px;border-bottom:2px solid rgba(255,255,255,0.15);"
             "position:sticky;top:0;z-index:1;background:#0e1117;")

    scroll_html  = "<div style='max-height:420px;overflow-y:auto;"
    scroll_html += "border:1px solid rgba(255,255,255,0.08);border-radius:4px;'>"
    scroll_html += "<div style='" + _GRID + _HDR + "'>"
    scroll_html += "<div>Rep ID</div><div>HCPs</div>"
    scroll_html += "<div style='color:#DC2626;opacity:1;'>Critical</div>"
    scroll_html += "<div style='color:#EA580C;opacity:1;'>High</div>"
    scroll_html += "<div style='color:#CA8A04;opacity:1;'>Medium</div>"
    scroll_html += "<div>Avg Risk</div>"
    scroll_html += "</div>"

    for i, (rep_id, stats) in enumerate(rep_rows):
        avg_risk = stats["total_risk"] / max(stats["hcp_count"], 1)
        row_bg   = "rgba(255,255,255,0.05)" if i % 2 == 0 else "transparent"
        _ROW = (_GRID + "font-size:15px;padding:10px 12px;"
                "background:" + row_bg + ";"
                "border-bottom:1px solid rgba(255,255,255,0.08);"
                "align-items:center;")
        scroll_html += "<div style='" + _ROW + "'>"
        scroll_html += "<div style='font-weight:700;color:#60a5fa;'>" + rep_id + "</div>"
        scroll_html += "<div style='font-weight:600;'>" + str(stats["hcp_count"]) + "</div>"
        scroll_html += "<div style='font-weight:800;color:#DC2626;'>" + str(stats["critical"]) + "</div>"
        scroll_html += "<div style='font-weight:800;color:#EA580C;'>" + str(stats["high"]) + "</div>"
        scroll_html += "<div style='font-weight:600;color:#CA8A04;'>" + str(stats["medium"]) + "</div>"
        scroll_html += "<div style='font-weight:700;'>" + str(int(avg_risk)) + "</div>"
        scroll_html += "</div>"

    scroll_html += "</div>"
    st.markdown(scroll_html, unsafe_allow_html=True)

with col_main:
    # ── Focus banner ───────────────────────────────────────────────────────────
    if hop_info:
        sel_id   = hop_info["selected_id"]
        is_rep   = hop_info["is_rep"]
        n1, n2   = hop_info["n_hop1"], hop_info["n_hop2"]
        label    = "Rep" if is_rep else "HCP"
        neighbor = "HCPs" if is_rep else "reps"
        second   = "reps' other HCPs" if is_rep else "HCPs' other reps"
        st.markdown(
            f"<div style='background:rgba(251,191,36,0.12);"
            f"border-left:4px solid #fbbf24;padding:10px 16px;"
            f"margin-bottom:8px;border-radius:4px;font-size:15px;'>"
            f"<strong>Focused: {label} {sel_id}</strong> "
            f"— {n1} direct {neighbor} · {n2} {second} in 2-hop</div>",
            unsafe_allow_html=True,
        )
        if st.button("Clear selection", key="clear_network_selection",
                     type="secondary"):
            st.session_state["network_selected_hcp"] = None
            st.session_state["selected_hcp_id"] = None
            st.rerun()

    # ── Graph ──────────────────────────────────────────────────────────────────
    if filtered_hcps:
        clicked_id = agraph(nodes=nodes, edges=edges, config=_AGRAPH_CONFIG)
        if clicked_id and clicked_id != st.session_state.get("network_selected_hcp"):
            st.session_state["network_selected_hcp"] = clicked_id
            st.session_state["selected_hcp_id"] = clicked_id
            st.rerun()
    else:
        st.info("No HCPs match the current filter.")

    st.caption(
        f"Showing {n_reps} reps · {len(filtered_hcps)} HCPs · "
        f"{len(edges)} edges · {state_label}"
    )
