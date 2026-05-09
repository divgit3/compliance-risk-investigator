# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

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
    st.page_link("pages/1_Compliance_Risk_Overview.py", label="📊 Overview", icon=None)
    st.page_link("pages/2_Rep_HCP_Network.py",          label="🕸️ Rep–HCP Network", icon=None)
    st.page_link("pages/3_HCP_Explorer.py",             label="🔎 HCP Explorer", icon=None)
    st.page_link("pages/4_HCP_Detail.py",               label="👤 HCP Detail", icon=None)
    st.page_link("pages/5_Policy_QA.py",                label="📋 Policy Q&A", icon=None)
    st.markdown("---")
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
