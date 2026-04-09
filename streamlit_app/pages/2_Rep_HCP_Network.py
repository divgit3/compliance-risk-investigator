"""
streamlit_app/pages/2_Rep_HCP_Network.py — Rep→HCP relationship network graph.

Data source: GET /hcps (FastAPI only — no parquet reads).
Rep-HCP edges unavailable in dev — rep_id not in API response.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict

import streamlit as st
import streamlit.components.v1 as components

from components.api_client import APIError, get_client
from config import FLAG_LABELS, RISK_TIER_COLORS, TIER_ORDER

st.set_page_config(
    page_title="Rep–HCP Network",
    layout="wide",
    page_icon="🕸️",
)

# ── Session state defaults ─────────────────────────────────────────────────────

if "filter_state" not in st.session_state:
    st.session_state["filter_state"] = []
if "filter_tier" not in st.session_state:
    st.session_state["filter_tier"] = []
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
    """Fetch up to 2000 HCPs for the network, filtered by tier."""
    client = get_client()
    tiers = _TIER_FILTER_MAP[tier_filter_key]
    all_hcps: list[dict] = []
    for tier in tiers:
        offset = 0
        limit = 500
        while len(all_hcps) < 2000:
            data = client.get("/hcps", params={"tier": tier, "limit": limit, "offset": offset})
            batch = data.get("hcps", [])
            if not batch:
                break
            all_hcps.extend(batch)
            offset += len(batch)
            if offset >= data.get("total", 0):
                break
            if len(all_hcps) >= 2000:
                break
    return all_hcps[:2000]


# ── Load data ─────────────────────────────────────────────────────────────────

with st.spinner("Loading HCP data…"):
    try:
        hcp_list = fetch_network_hcps(st.session_state["network_tier_filter"])
    except APIError as e:
        st.error(f"API error: {e}")
        st.stop()

# Apply global tier filter from session state (if set, override network filter)
if st.session_state["filter_tier"]:
    hcp_list = [h for h in hcp_list if h.get("risk_tier") in st.session_state["filter_tier"]]

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

    st.markdown("**GLOBAL FILTERS**")

    active_states: list[str] = st.session_state["filter_state"]
    active_tiers: list[str]  = st.session_state["filter_tier"]

    if not active_states and not active_tiers:
        st.markdown("_<span style='color:#888'>No filters active</span>_", unsafe_allow_html=True)
    else:
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
                f"color:{color}'>Tier: {t}</span>",
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

    st.markdown("---")
    st.markdown("**LEGEND**")
    for tier, color in RISK_TIER_COLORS.items():
        st.markdown(
            f"<span style='color:{color};font-size:1.1em'>⬤</span> &nbsp;"
            f"{tier.capitalize()} HCP",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<span style='color:#9CA3AF;font-size:1.1em'>▪</span> &nbsp;"
        "<span style='color:#9CA3AF'>Rep node (unavailable in dev)</span>",
        unsafe_allow_html=True,
    )

# ── Page header ────────────────────────────────────────────────────────────────

hdr_left, hdr_right = st.columns([3, 1])
with hdr_left:
    st.markdown("## Rep–HCP Network")
    st.caption("Default: critical HCPs only · click a node to see details")

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

# ── Build pyvis network ────────────────────────────────────────────────────────

def _build_pyvis_html(hcps: list[dict]) -> str:
    """Build a pyvis Network from HCP list and return HTML string."""
    from pyvis.network import Network

    net = Network(
        height="500px",
        width="100%",
        bgcolor="#ffffff",
        font_color="black",
    )
    net.force_atlas_2based(
        gravity=-50,
        central_gravity=0.01,
        spring_length=100,
        spring_strength=0.08,
        damping=0.4,
        overlap=0,
    )

    for hcp in hcps:
        hcp_id    = str(hcp.get("hcp_id", ""))
        score     = float(hcp.get("risk_score", 0))
        tier      = hcp.get("risk_tier", "low")
        color     = RISK_TIER_COLORS.get(tier, "#16A34A")
        label     = hcp_id[-6:] if len(hcp_id) >= 6 else hcp_id
        size      = 10 + (score / 100) * 20  # 10–30
        tooltip   = f"HCP: {hcp_id}\nScore: {score:.0f}\nTier: {tier}"

        net.add_node(
            hcp_id,
            label=label,
            title=tooltip,
            color=color,
            size=size,
            shape="circle",
        )

    net.set_options("""
{
  "physics": {
    "enabled": true,
    "solver": "repulsion",
    "repulsion": {
      "nodeDistance": 100,
      "springLength": 200,
      "springConstant": 0.05,
      "damping": 0.09
    }
  }
}
""")

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        net.save_graph(f.name)
        f.flush()
        with open(f.name, "r") as fread:
            return fread.read()


with col_net:
    if hcp_list:
        try:
            html_content = _build_pyvis_html(hcp_list)
            components.html(html_content, height=520, scrolling=False)
        except ImportError as e:
            st.warning(f"Import error: {e}")
        except Exception as e:
            st.error(f"Network render error: {e}")
    else:
        st.info("No HCPs match the current filter.")

    st.caption(
        "Rep–HCP edges unavailable in dev — rep_id field not in API response. "
        "Edges will populate after schema fix."
    )

    # ── Top 10 riskiest HCPs table ─────────────────────────────────────────────

    st.markdown("#### Top 10 Riskiest HCPs")

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
    # Node selection via selectbox (pyvis iframe can't trigger Streamlit reruns)
    hcp_options = ["— select —"] + [str(h.get("hcp_id", "")) for h in hcp_list]
    selected_label = st.selectbox(
        "Select HCP to inspect:",
        options=hcp_options,
        index=0,
        key="network_hcp_selectbox",
    )

    selected_hcp_dict: dict | None = None
    if selected_label and selected_label != "— select —":
        st.session_state["network_selected_hcp"] = selected_label
        selected_hcp_dict = next(
            (h for h in hcp_list if str(h.get("hcp_id", "")) == selected_label),
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
            "<span style='color:#9CA3AF'>Click a node or select an HCP above "
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
