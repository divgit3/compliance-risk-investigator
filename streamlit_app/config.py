"""
streamlit_app/config.py — Shared constants for the Streamlit dashboard.
"""

API_BASE_URL = "http://localhost:8000"

RISK_TIER_COLORS = {
    "critical": "#DC2626",  # red
    "high":     "#EA580C",  # orange
    "medium":   "#CA8A04",  # amber
    "low":      "#16A34A",  # green
}

TIER_ORDER = ["critical", "high", "medium", "low"]

PAGE_SIZE = 500           # rows fetched per /hcps call when aggregating
MAX_HCPS_FOR_OVERVIEW = 5000  # cap for overview aggregation
