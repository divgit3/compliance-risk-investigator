"""
streamlit_app/pages/3_HCP_Explorer.py — Browse and filter the full HCP list.

Data source: GET /hcps (FastAPI only — no parquet reads).
"""

from __future__ import annotations

import streamlit as st

from components.api_client import APIError, get_client
from config import FLAG_LABELS, RISK_TIER_COLORS, TIER_ORDER

st.set_page_config(
    page_title="HCP Explorer",
    layout="wide",
    page_icon="🔎",
)

# ── Session state defaults ─────────────────────────────────────────────────────

if "filter_state" not in st.session_state:
    st.session_state["filter_state"] = []
if "filter_tier" not in st.session_state:
    st.session_state["filter_tier"] = []
if "explorer_tier" not in st.session_state:
    st.session_state["explorer_tier"] = "all"
if "explorer_page" not in st.session_state:
    st.session_state["explorer_page"] = 1
if "selected_hcp_id" not in st.session_state:
    st.session_state["selected_hcp_id"] = None

# ── Data fetching ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_hcp_page(tier: str | None, limit: int, offset: int) -> dict:
    """Fetch a single page of HCPs. tier=None fetches all tiers."""
    params: dict = {"limit": limit, "offset": offset}
    if tier and tier != "all":
        params["tier"] = tier
    return get_client().get("/hcps", params=params)


@st.cache_data(ttl=300)
def fetch_tier_total(tier: str | None) -> int:
    """Return total HCP count for a tier (or all tiers)."""
    params: dict = {"limit": 1}
    if tier and tier != "all":
        params["tier"] = tier
    data = get_client().get("/hcps", params=params)
    return data.get("total", 0)


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

    # Global filters
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

    st.markdown("---")

    # Page-specific filters
    st.markdown("**SEARCH & DISPLAY**")

    search_text = st.text_input(
        "Search HCP ID",
        placeholder="e.g. HCP_357811",
        key="explorer_search",
    )

    rows_per_page = st.select_slider(
        "Rows per page",
        options=[10, 25, 50, 100],
        value=10,
        key="explorer_rows",
    )


# ── Load tier totals ───────────────────────────────────────────────────────────

try:
    total_all      = fetch_tier_total("all")
    total_critical = fetch_tier_total("critical")
    total_high     = fetch_tier_total("high")
    total_medium   = fetch_tier_total("medium")
    total_low      = fetch_tier_total("low")
except APIError as e:
    st.error(f"API error: {e}")
    st.stop()

# ── Page header ────────────────────────────────────────────────────────────────

st.markdown("## HCP Explorer")
st.caption("Default: top 10 by risk score · Click HCP ID → HCP Detail")

# ── Tier filter cards ──────────────────────────────────────────────────────────

_TIER_CARDS = [
    ("all",      "All",      total_all,      "#6B7280"),
    ("critical", "Critical", total_critical, RISK_TIER_COLORS["critical"]),
    ("high",     "High",     total_high,     RISK_TIER_COLORS["high"]),
    ("medium",   "Medium",   total_medium,   RISK_TIER_COLORS["medium"]),
    ("low",      "Low",      total_low,      RISK_TIER_COLORS["low"]),
]

card_cols = st.columns(5)
for col, (tier_key, tier_label, tier_count, tier_color) in zip(card_cols, _TIER_CARDS):
    is_active = st.session_state["explorer_tier"] == tier_key
    border_style = f"3px solid {tier_color}" if is_active else "1px solid #E5E7EB"
    bg_style = f"{tier_color}11" if is_active else "white"

    col.markdown(
        f"<div style='border:{border_style};border-radius:8px;padding:12px 8px;"
        f"background:{bg_style};text-align:center;'>"
        f"<div style='font-size:1.4em;font-weight:700;color:{tier_color}'>{tier_count:,}</div>"
        f"<div style='font-size:0.85em;color:#6B7280'>{tier_label}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if col.button(f"Filter: {tier_label}", key=f"card_{tier_key}", use_container_width=True,
                  help=f"Show {tier_label} HCPs"):
        if st.session_state["explorer_tier"] != tier_key:
            st.session_state["explorer_tier"] = tier_key
            st.session_state["explorer_page"] = 1
            st.rerun()

st.markdown("")

# ── Determine active tier ──────────────────────────────────────────────────────

# Global session filter overrides the card filter if set
active_tier = st.session_state["explorer_tier"]
if st.session_state["filter_tier"]:
    # Use first global tier filter if set
    active_tier = st.session_state["filter_tier"][0]

# Determine total for pagination
_tier_total_map = {
    "all":      total_all,
    "critical": total_critical,
    "high":     total_high,
    "medium":   total_medium,
    "low":      total_low,
}
current_total = _tier_total_map.get(active_tier, total_all)
total_pages   = max(1, (current_total + rows_per_page - 1) // rows_per_page)

# Clamp page within range
if st.session_state["explorer_page"] > total_pages:
    st.session_state["explorer_page"] = total_pages

current_page = st.session_state["explorer_page"]
offset       = (current_page - 1) * rows_per_page

# ── Fetch current page ────────────────────────────────────────────────────────

try:
    page_data = fetch_hcp_page(active_tier, rows_per_page, offset)
    hcp_rows  = page_data.get("hcps", [])
except APIError as e:
    st.error(f"API error: {e}")
    st.stop()

# ── Apply search filter client-side ───────────────────────────────────────────

if search_text:
    search_lower  = search_text.lower()
    hcp_rows = [h for h in hcp_rows if search_lower in str(h.get("hcp_id", "")).lower()]
    if hcp_rows:
        st.info(f"{len(hcp_rows)} result(s) matching '{search_text}' on this page")
    else:
        st.info(f"No HCPs match '{search_text}' on this page")

# ── Pagination controls (top) ─────────────────────────────────────────────────

a = offset + 1
b = min(offset + rows_per_page, current_total)
st.caption(
    f"Page {current_page} of {total_pages} · "
    f"Showing {a:,}–{b:,} of {current_total:,} HCPs · "
    f"Sorted: risk score desc"
)

pag_prev, pag_next, pag_spacer = st.columns([1, 1, 8])
with pag_prev:
    if st.button("← Prev", disabled=(current_page <= 1), use_container_width=True):
        st.session_state["explorer_page"] -= 1
        st.rerun()
with pag_next:
    if st.button("Next →", disabled=(current_page >= total_pages), use_container_width=True):
        st.session_state["explorer_page"] += 1
        st.rerun()

st.markdown("---")

# ── HCP table ─────────────────────────────────────────────────────────────────

# Header row
hdr = st.columns([2, 3, 1])
for col, label in zip(hdr, ["HCP ID", "Risk Score", "Tier"]):
    col.markdown(f"**{label}**")

st.markdown(
    "<hr style='margin:4px 0 8px 0;border:none;border-top:1px solid #E5E7EB'>",
    unsafe_allow_html=True,
)

for hcp in hcp_rows:
    hcp_id     = str(hcp.get("hcp_id", "—"))
    risk_score = float(hcp.get("risk_score", 0))
    tier       = hcp.get("risk_tier", "low")
    tier_color = RISK_TIER_COLORS.get(tier, "#888")

    row = st.columns([2, 3, 1])

    # col1: clickable HCP ID button
    if row[0].button(hcp_id, key=f"hcp_{hcp_id}"):
        st.session_state["selected_hcp_id"] = hcp_id
        st.switch_page("pages/4_HCP_Detail.py")

    # col2: colored HTML progress bar + score
    bar_color = RISK_TIER_COLORS.get(tier, "#16A34A")
    row[1].markdown(
        f"""
        <div style="background:#e5e7eb;border-radius:999px;height:8px;width:100%;">
          <div style="background:{bar_color};width:{risk_score:.0f}%;
               height:8px;border-radius:999px;"></div>
        </div>
        <div style="font-size:12px;color:#6b7280;margin-top:2px;">
          {risk_score:.0f} / 100
        </div>
        """,
        unsafe_allow_html=True,
    )

    # col3: colored tier badge
    row[2].markdown(
        f"<span style='color:{tier_color};font-weight:600'>{tier.capitalize()}</span>",
        unsafe_allow_html=True,
    )

st.caption("Top flag column coming in polish pass — requires per-HCP flags endpoint.")
st.markdown("---")

# ── Pagination controls (bottom) ──────────────────────────────────────────────

bot_prev, bot_next, bot_spacer = st.columns([1, 1, 8])
with bot_prev:
    if st.button("← Prev", key="bot_prev", disabled=(current_page <= 1), use_container_width=True):
        st.session_state["explorer_page"] -= 1
        st.rerun()
with bot_next:
    if st.button("Next →", key="bot_next", disabled=(current_page >= total_pages), use_container_width=True):
        st.session_state["explorer_page"] += 1
        st.rerun()
