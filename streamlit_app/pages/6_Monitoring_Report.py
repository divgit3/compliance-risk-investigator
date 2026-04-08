"""
streamlit_app/pages/6_Monitoring_Report.py — Full MonitoringAgent population report.
"""

import streamlit as st

from components.api_client import APIError, get_client  # noqa: F401
from components.charts import risk_tier_bar, top_flags_bar  # noqa: F401

st.set_page_config(page_title="Monitoring Report", layout="wide", page_icon="🔍")
st.title("Monitoring Report")
st.info("Coming in Task 4.6")
