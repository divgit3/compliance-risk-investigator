"""
streamlit_app/app.py — Entry point for the Compliance Risk Investigator dashboard.

Run:
    streamlit run streamlit_app/app.py
"""

import streamlit as st

from components.api_client import APIError, get_client
from config import RISK_TIER_COLORS

st.set_page_config(
    page_title="Compliance Risk Investigator",
    layout="wide",
    page_icon="🔍",
)

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🔍 Compliance Risk AI")
    st.markdown("---")

    # API health indicator
    try:
        get_client().get("/health")
        st.markdown("🟢 &nbsp;API connected", unsafe_allow_html=True)
    except APIError:
        st.markdown("🔴 &nbsp;API unreachable", unsafe_allow_html=True)

    st.markdown("---")
    st.caption("Nova Pharma Inc — Compliance Platform")

# ── Main ───────────────────────────────────────────────────────────────────────

# Check API availability and show a warning banner if down
try:
    get_client().get("/health")
except APIError as e:
    st.warning(
        f"⚠️ Cannot reach the FastAPI backend at `http://localhost:8000`. "
        f"Start the API with `uvicorn api.main:app --port 8000` and refresh.\n\n"
        f"Error: {e}"
    )
    st.stop()

st.title("🔍 Compliance Risk Investigator")
st.markdown(
    "Use the sidebar to navigate between pages. "
    "All data is sourced live from the FastAPI backend."
)
