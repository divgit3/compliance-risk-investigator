"""
streamlit_app/pages/5_Policy_QA.py — Natural language policy Q&A via PolicyAgent.
"""

import streamlit as st

from components.api_client import APIError, get_client  # noqa: F401

st.set_page_config(page_title="Policy Q&A", layout="wide", page_icon="🔍")
st.title("Policy Q&A")
st.info("Coming in Task 4.5")
