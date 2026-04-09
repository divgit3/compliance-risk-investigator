"""
streamlit_app/pages/4_HCP_Detail.py — Per-HCP investigation report + SHAP explanations.
"""

import streamlit as st

from components.api_client import APIError, get_client  # noqa: F401
from components.charts import top_flags_bar  # noqa: F401

st.set_page_config(page_title="HCP Detail", layout="wide", page_icon="🔍")
st.title("HCP Detail")
st.info("Coming in Task 4.4")
