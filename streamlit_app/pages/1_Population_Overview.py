"""
streamlit_app/pages/1_Population_Overview.py — Population-level compliance risk overview.

Data sources (FastAPI only):
  GET /hcps            — HCP list with risk scores and flag counts
  GET /monitoring      — MonitoringAgent population analysis
"""

from __future__ import annotations

from collections import defaultdict

import pydeck as pdk
import streamlit as st

from components.api_client import APIError, get_client
from components.charts import risk_tier_bar, risk_tier_pie, top_flags_bar
from config import MAX_HCPS_FOR_OVERVIEW, PAGE_SIZE, TIER_ORDER

st.set_page_config(page_title="Population Overview", layout="wide", page_icon="🔍")

# ── State centroids (lat/lng) ──────────────────────────────────────────────────

_STATE_COORDS: dict[str, tuple[float, float]] = {
    "AL": (32.806671, -86.791130), "AK": (61.370716, -152.404419),
    "AZ": (33.729759, -111.431221), "AR": (34.969704, -92.373123),
    "CA": (36.116203, -119.681564), "CO": (39.059811, -105.311104),
    "CT": (41.597782, -72.755371), "DE": (39.318523, -75.507141),
    "FL": (27.766279, -81.686783), "GA": (33.040619, -83.643074),
    "HI": (21.094318, -157.498337), "ID": (44.240459, -114.478828),
    "IL": (40.349457, -88.986137), "IN": (39.849426, -86.258278),
    "IA": (42.011539, -93.210526), "KS": (38.526600, -96.726486),
    "KY": (37.668140, -84.670067), "LA": (31.169960, -91.867805),
    "ME": (44.693947, -69.381927), "MD": (39.063946, -76.802101),
    "MA": (42.230171, -71.530106), "MI": (43.326618, -84.536095),
    "MN": (45.694454, -93.900192), "MS": (32.741646, -89.678696),
    "MO": (38.456085, -92.288368), "MT": (46.921925, -110.454353),
    "NE": (41.125370, -98.268082), "NV": (38.313515, -117.055374),
    "NH": (43.452492, -71.563896), "NJ": (40.298904, -74.521011),
    "NM": (34.840515, -106.248482), "NY": (42.165726, -74.948051),
    "NC": (35.630066, -79.806419), "ND": (47.528912, -99.784012),
    "OH": (40.388783, -82.764915), "OK": (35.565342, -96.928917),
    "OR": (44.572021, -122.070938), "PA": (40.590752, -77.209755),
    "RI": (41.680893, -71.511780), "SC": (33.856892, -80.945007),
    "SD": (44.299782, -99.438828), "TN": (35.747845, -86.692345),
    "TX": (31.054487, -97.563461), "UT": (40.150032, -111.862434),
    "VT": (44.045876, -72.710686), "VA": (37.769337, -78.169968),
    "WA": (47.400902, -121.490494), "WV": (38.491226, -80.954453),
    "WI": (44.268543, -89.616508), "WY": (42.755966, -107.302490),
    "DC": (38.897438, -77.026817),
}

# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_hcps() -> list[dict]:
    """Fetch up to MAX_HCPS_FOR_OVERVIEW HCPs from /hcps, paginating by PAGE_SIZE."""
    client = get_client()
    all_hcps: list[dict] = []
    offset = 0
    while len(all_hcps) < MAX_HCPS_FOR_OVERVIEW:
        data = client.get("/hcps", params={"limit": PAGE_SIZE, "offset": offset})
        batch = data.get("hcps", [])
        if not batch:
            break
        all_hcps.extend(batch)
        offset += len(batch)
        if offset >= data.get("total", 0):
            break
    return all_hcps


def load_monitoring() -> dict:
    """Fetch monitoring report — not cached (agent endpoint, on-demand only)."""
    return get_client().get("/monitoring")


# ── Sidebar filters ────────────────────────────────────────────────────────────

with st.spinner("Loading population data…"):
    try:
        hcps = load_hcps()
    except APIError as e:
        st.error(f"API error: {e}")
        st.stop()

all_states = sorted({h.get("state", "") for h in hcps if h.get("state")})

with st.sidebar:
    st.markdown("## 🔍 Compliance Risk AI")
    st.markdown("---")
    st.markdown("### Filters")

    selected_states = st.multiselect(
        "State", options=all_states, default=[], placeholder="All states"
    )
    selected_tiers = st.multiselect(
        "Risk tier", options=TIER_ORDER, default=[], placeholder="All tiers"
    )

# Apply filters client-side
filtered = hcps
if selected_states:
    filtered = [h for h in filtered if h.get("state") in selected_states]
if selected_tiers:
    filtered = [h for h in filtered if h.get("risk_tier") in selected_tiers]

# ── KPI row ────────────────────────────────────────────────────────────────────

st.title("Population Overview")
st.caption(f"Showing {len(filtered):,} of {len(hcps):,} HCPs (capped at {MAX_HCPS_FOR_OVERVIEW:,})")

tier_counts: dict[str, int] = defaultdict(int)
any_flag_count = 0
for h in filtered:
    tier = h.get("risk_tier", "low")
    tier_counts[tier] += 1
    if (h.get("total_rule_flags") or 0) > 0:
        any_flag_count += 1

total = len(filtered)
any_flag_pct = (any_flag_count / total * 100) if total else 0.0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total HCPs", f"{total:,}")
col2.metric("Critical", f"{tier_counts.get('critical', 0):,}")
col3.metric("High", f"{tier_counts.get('high', 0):,}")
col4.metric("Any-Flag %", f"{any_flag_pct:.1f}%")

st.markdown("---")

# ── Charts row ────────────────────────────────────────────────────────────────

left_col, right_col = st.columns(2)

with left_col:
    st.plotly_chart(risk_tier_pie(dict(tier_counts)), use_container_width=True)
    st.plotly_chart(risk_tier_bar(dict(tier_counts)), use_container_width=True)

with right_col:
    # /hcps list records don't include fired_flags detail — use monitoring top_flags
    # for population-level flag breakdown; fall back gracefully if unavailable.
    flag_counts: dict[str, int] = {}
    try:
        mon = load_monitoring()
        top_flags_raw = mon.get("top_flags", [])
        if top_flags_raw:
            flag_counts = {f["flag_name"]: f["count"] for f in top_flags_raw}
    except APIError:
        pass

    if flag_counts:
        st.plotly_chart(top_flags_bar(flag_counts, top_n=10), use_container_width=True)
    else:
        st.info("Flag breakdown unavailable from API")

st.markdown("---")

# ── Geographic heatmap ────────────────────────────────────────────────────────

st.subheader("Geographic Risk Heatmap")

# Aggregate per state from filtered HCPs
state_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "critical": 0, "high": 0})
for h in filtered:
    state = h.get("state")
    if not state:
        continue
    state_stats[state]["total"] += 1
    tier = h.get("risk_tier", "low")
    if tier == "critical":
        state_stats[state]["critical"] += 1
    elif tier == "high":
        state_stats[state]["high"] += 1

# If HCP records lack state field, fall back to monitoring high_risk_segments
if not state_stats:
    try:
        mon_data = load_monitoring()
        for seg in mon_data.get("high_risk_segments", []):
            if seg.get("segment_type") == "state":
                s = seg["segment_value"]
                state_stats[s]["total"] = seg.get("hcp_count", 0)
                state_stats[s]["critical"] = seg.get("critical_count", 0)
                state_stats[s]["high"] = seg.get("high_count", 0)
    except APIError:
        pass

map_rows = []
for state, stats in state_stats.items():
    coords = _STATE_COORDS.get(state)
    if not coords:
        continue
    lat, lng = coords
    high_crit = stats["critical"] + stats["high"]
    color = [220, 38, 38, 180] if stats["critical"] > 0 else [234, 88, 12, 160]
    map_rows.append({
        "state": state,
        "lat": lat,
        "lng": lng,
        "high_critical": high_crit,
        "total": stats["total"],
        "radius": max(20000, high_crit * 800),
        "color": color,
    })

if map_rows:
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_rows,
        get_position="[lng, lat]",
        get_radius="radius",
        get_fill_color="color",
        pickable=True,
        opacity=0.7,
    )
    view = pdk.ViewState(latitude=39.5, longitude=-98.35, zoom=3, pitch=0)
    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=view,
        tooltip={"text": "{state}\nHigh+Critical: {high_critical}\nTotal: {total}"},
        map_style="mapbox://styles/mapbox/light-v9",
    )
    st.pydeck_chart(deck)
else:
    st.info("No geographic data available — HCP records do not include state field.")

st.markdown("---")

# ── Systemic issues panel ─────────────────────────────────────────────────────

with st.expander("⚠️ Systemic Issues", expanded=True):
    try:
        monitoring = load_monitoring()
        issues = monitoring.get("systemic_issues", [])
        if issues:
            for issue in issues:
                severity = issue.get("severity", "medium")
                desc = issue.get("description", "")
                rec = issue.get("recommendation", "")
                affected = issue.get("affected_hcp_count", 0)
                msg = f"**{issue.get('issue_type', 'Issue').replace('_', ' ').title()}** — {desc}"
                if rec:
                    msg += f"\n\n_Recommendation: {rec}_"
                if affected:
                    msg += f"\n\n_{affected:,} HCPs affected_"
                if severity == "critical":
                    st.error(msg)
                else:
                    st.warning(msg)
        else:
            st.success("No systemic issues detected")
    except APIError as e:
        st.error(f"API error loading monitoring report: {e}")
