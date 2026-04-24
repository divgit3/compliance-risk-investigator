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

if "explorer_tier" not in st.session_state:
    st.session_state["explorer_tier"] = "all"
if "explorer_page" not in st.session_state:
    st.session_state["explorer_page"] = 1
if "selected_hcp_id" not in st.session_state:
    st.session_state["selected_hcp_id"] = None
if "explorer_specialty" not in st.session_state:
    st.session_state["explorer_specialty"] = "All"
if "explorer_state" not in st.session_state:
    st.session_state["explorer_state"] = "All"

# ── Data fetching ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_filter_options() -> dict:
    """Return distinct specialty and state values for filter dropdowns."""
    try:
        return get_client().get("/hcps/filter-options")
    except Exception:
        return {"specialties": [], "states": []}


@st.cache_data(ttl=300)
def fetch_hcp_page(tier: str | None, specialty: str | None, state: str | None, limit: int, offset: int) -> dict:
    """Fetch a single page of HCPs. tier=None fetches all tiers."""
    params: dict = {"limit": limit, "offset": offset}
    if tier and tier != "all":
        params["tier"] = tier
    if specialty and specialty != "All":
        params["specialty"] = specialty
    if state and state != "All":
        params["state"] = state
    return get_client().get("/hcps", params=params)


@st.cache_data(ttl=300)
def fetch_tier_total(tier: str | None) -> int:
    """Return total HCP count for a tier (or all tiers)."""
    params: dict = {"limit": 1}
    if tier and tier != "all":
        params["tier"] = tier
    data = get_client().get("/hcps", params=params)
    return data.get("total", 0)


@st.cache_data(ttl=300)
def fetch_filtered_total(tier: str | None, specialty: str | None, state: str | None) -> int:
    """Return total HCP count with all active filters applied."""
    params: dict = {"limit": 1}
    if tier and tier != "all":
        params["tier"] = tier
    if specialty and specialty != "All":
        params["specialty"] = specialty
    if state and state != "All":
        params["state"] = state
    data = get_client().get("/hcps", params=params)
    return data.get("total", 0)


@st.cache_data(ttl=300)
def fetch_hcp_top_flag(hcp_id: str) -> str:
    """Fetch the top flag for a single HCP from /hcps/{id}/flags."""
    try:
        data = get_client().get(f"/hcps/{hcp_id}/flags")
        fired_flags = data.get("fired_flags", [])
        if not fired_flags:
            return "—"
        top_flag = fired_flags[0]
        return FLAG_LABELS.get(top_flag, top_flag.replace("_", " ").title())
    except Exception:
        return "—"


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
    bg_style = f"{tier_color}22" if is_active else "transparent"

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

# ── Specialty / State filter dropdowns ────────────────────────────────────────

filter_options = fetch_filter_options()
specialty_options = ["All"] + filter_options.get("specialties", [])
state_options     = ["All"] + filter_options.get("states", [])

filt_col1, filt_col2 = st.columns(2)
with filt_col1:
    new_specialty = st.selectbox(
        "Specialty",
        options=specialty_options,
        index=specialty_options.index(st.session_state["explorer_specialty"])
              if st.session_state["explorer_specialty"] in specialty_options else 0,
        key="specialty_select",
    )
with filt_col2:
    new_state = st.selectbox(
        "State",
        options=state_options,
        index=state_options.index(st.session_state["explorer_state"])
              if st.session_state["explorer_state"] in state_options else 0,
        key="state_select",
    )

if new_specialty != st.session_state["explorer_specialty"] or new_state != st.session_state["explorer_state"]:
    st.session_state["explorer_specialty"] = new_specialty
    st.session_state["explorer_state"]     = new_state
    st.session_state["explorer_page"]      = 1
    st.rerun()

active_specialty = st.session_state["explorer_specialty"]
active_state     = st.session_state["explorer_state"]

# ── Determine active tier ──────────────────────────────────────────────────────

active_tier = st.session_state["explorer_tier"]

# Use filtered total when any filter is active, otherwise use pre-fetched tier total
_tier_total_map = {
    "all":      total_all,
    "critical": total_critical,
    "high":     total_high,
    "medium":   total_medium,
    "low":      total_low,
}
_filters_active = (active_specialty != "All") or (active_state != "All")
if _filters_active:
    current_total = fetch_filtered_total(active_tier, active_specialty, active_state)
else:
    current_total = _tier_total_map.get(active_tier, total_all)

total_pages   = max(1, (current_total + rows_per_page - 1) // rows_per_page)

# Clamp page within range
if st.session_state["explorer_page"] > total_pages:
    st.session_state["explorer_page"] = total_pages

current_page = st.session_state["explorer_page"]
offset       = (current_page - 1) * rows_per_page

# ── Fetch current page ────────────────────────────────────────────────────────

try:
    page_data = fetch_hcp_page(active_tier, active_specialty, active_state, rows_per_page, offset)
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

_filter_parts = []
if active_specialty != "All":
    _filter_parts.append(active_specialty)
if active_state != "All":
    _filter_parts.append(active_state)
_filter_suffix = f" · Filtered: {', '.join(_filter_parts)}" if _filter_parts else ""

a = offset + 1
b = min(offset + rows_per_page, current_total)
st.caption(
    f"Page {current_page} of {total_pages} · "
    f"Showing {a:,}–{b:,} of {current_total:,} HCPs · "
    f"Sorted: risk score desc{_filter_suffix}"
)

st.markdown("""
<style>
div[data-testid="stHorizontalBlock"] button {
    font-weight: 700 !important;
    opacity: 1 !important;
}
</style>
""", unsafe_allow_html=True)
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

if not hcp_rows and not search_text:
    _active_filters = [p for p in [
        active_specialty if active_specialty != "All" else None,
        active_state     if active_state     != "All" else None,
    ] if p]
    if _active_filters:
        st.info(f"No HCPs found for the current filters ({', '.join(_active_filters)}). Try a different combination.")
    else:
        st.info("No HCPs found.")
    st.stop()

# Fetch top flags for current page (cached, fast after first load)
top_flags = {
    hcp["hcp_id"]: fetch_hcp_top_flag(hcp["hcp_id"])
    for hcp in hcp_rows
}

# Header row
_COL_RATIOS = [1.2, 1.3, 0.5, 1.5, 0.7, 1.2]
hdr = st.columns(_COL_RATIOS)
for col, label in zip(hdr, ["HCP ID", "Specialty", "State", "Risk Score", "Tier", "Top Flag"]):
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
    top_flag   = top_flags.get(hcp_id, "—")
    specialty  = hcp.get("specialty") or "—"
    state      = hcp.get("state") or "—"

    row = st.columns(_COL_RATIOS)

    # col0: clickable HCP ID button
    if row[0].button(hcp_id, key=f"hcp_{hcp_id}"):
        st.session_state["selected_hcp_id"] = hcp_id
        st.switch_page("pages/4_HCP_Detail.py")

    # col1: specialty
    row[1].write(specialty)

    # col2: state abbreviation
    row[2].write(state)

    # col3: colored HTML progress bar + score
    bar_color = RISK_TIER_COLORS.get(tier, "#16A34A")
    row[3].markdown(
        f"""
        <div style="background:rgba(255,255,255,0.12);border-radius:999px;height:8px;width:100%;">
          <div style="background:{bar_color};width:{risk_score:.0f}%;
               height:8px;border-radius:999px;"></div>
        </div>
        <div style="font-size:12px;color:#6b7280;margin-top:2px;">
          {risk_score:.0f} / 100
        </div>
        """,
        unsafe_allow_html=True,
    )

    # col4: colored tier badge
    row[4].markdown(
        f"<span style='color:{tier_color};font-weight:600'>{tier.capitalize()}</span>",
        unsafe_allow_html=True,
    )

    # col5: top flag label
    row[5].markdown(
        f"<span style='font-size:12px;color:#374151'>{top_flag}</span>",
        unsafe_allow_html=True,
    )

st.markdown("---")

# ── Pagination controls (bottom) ──────────────────────────────────────────────

st.markdown("""
<style>
div[data-testid="stHorizontalBlock"] button {
    font-weight: 700 !important;
    opacity: 1 !important;
}
</style>
""", unsafe_allow_html=True)
bot_prev, bot_next, bot_spacer = st.columns([1, 1, 8])
with bot_prev:
    if st.button("← Prev", key="bot_prev", disabled=(current_page <= 1), use_container_width=True):
        st.session_state["explorer_page"] -= 1
        st.rerun()
with bot_next:
    if st.button("Next →", key="bot_next", disabled=(current_page >= total_pages), use_container_width=True):
        st.session_state["explorer_page"] += 1
        st.rerun()
