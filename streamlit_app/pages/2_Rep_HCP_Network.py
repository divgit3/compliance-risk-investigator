"""
streamlit_app/pages/2_Rep_HCP_Network.py — Rep→HCP relationship network graph.
"""

import streamlit as st

from components.api_client import APIError, get_client  # noqa: F401
from components.network import build_network  # noqa: F401

st.set_page_config(page_title="Rep–HCP Network", layout="wide", page_icon="🔍")
st.title("Rep–HCP Network")
st.info("Coming in Task 4.2")
