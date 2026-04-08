"""
streamlit_app/pages/2_HCP_Explorer.py — Browse and filter the full HCP list.
"""

import streamlit as st

from components.api_client import APIError, get_client  # noqa: F401
from config import TIER_ORDER  # noqa: F401

st.set_page_config(page_title="HCP Explorer", layout="wide", page_icon="🔍")
st.title("HCP Explorer")
st.info("Coming in Task 4.2")
