"""
streamlit_app/config.py — Shared constants for the Streamlit dashboard.
"""

API_BASE_URL = "http://localhost:8001"

RISK_TIER_COLORS = {
    "critical": "#DC2626",  # red
    "high":     "#EA580C",  # orange
    "medium":   "#CA8A04",  # amber
    "low":      "#16A34A",  # green
}

TIER_ORDER = ["critical", "high", "medium", "low"]

PAGE_SIZE = 500               # rows fetched per /hcps call when aggregating
MAX_HCPS_FOR_OVERVIEW = 5000  # cap for overview aggregation

FLAG_LABELS = {
    # Phase 2 rule names (original)
    "speaker_fmv_exceeded":             "Speaker pay above fair market",
    "annual_cap_exceeded":              "Annual spend cap breached",
    "annual_cap_warning":               "Approaching annual spend cap",
    "meal_cap_exceeded":                "Meal limit exceeded",
    "meal_cap_warning":                 "Approaching meal limit",
    "speaker_event_frequency":          "Excessive speaker events",
    "aggregate_spend_flag":             "High aggregate spend",
    "no_gap_between_events":            "No gap between events",
    # Phase 3 API flag names
    "flag_speaker_fmv_breach":          "Speaker pay above fair market",
    "flag_speaker_fmv_chronic":         "Chronic speaker FMV violations",
    "flag_repeat_speaker":              "Repeat speaker pattern",
    "flag_rapid_repeat_pattern":        "Rapid repeat engagements",
    "flag_missing_attestation":         "Missing interaction attestation",
    "flag_chronic_missing_attestation": "Chronic missing attestations",
    "flag_vague_rationale":             "Vague interaction rationale",
    "flag_vague_rationale_pattern":     "Pattern of vague rationales",
    "flag_fmv_non_compliance":          "FMV non-compliance",
    "flag_escalating_spend":            "Escalating spend pattern",
    "flag_low_attendance_pattern":      "Low speaker event attendance",
    "flag_aggregate_spend":             "High aggregate spend",
    "flag_annual_cap_warning":          "Approaching annual spend cap",
    "flag_annual_cap_breach":           "Annual spend cap breached",
}

SHAP_LABELS = {
    "speaker_events_count": "Speaker events attended",
    "total_spend_ytd":      "Total spend this year",
    "meal_count_90d":       "Meals in last 90 days",
    "fmv_ratio":            "Speaker pay vs fair market",
    "peer_spend_delta":     "Spend vs peer average",
}

US_STATE_CENTROIDS = {
    "AL": (32.8, -86.8), "AK": (64.2, -153.4), "AZ": (34.3, -111.1),
    "AR": (34.8, -92.2), "CA": (36.8, -119.4), "CO": (39.0, -105.5),
    "CT": (41.6, -72.7), "DE": (39.0, -75.5),  "FL": (27.8, -81.5),
    "GA": (32.2, -83.4), "HI": (20.3, -156.4), "ID": (44.2, -114.5),
    "IL": (40.3, -89.0), "IN": (39.9, -86.3),  "IA": (42.0, -93.2),
    "KS": (38.5, -98.4), "KY": (37.5, -85.3),  "LA": (31.1, -91.9),
    "ME": (45.4, -69.0), "MD": (39.0, -76.8),  "MA": (42.3, -71.8),
    "MI": (44.3, -85.4), "MN": (46.4, -93.1),  "MS": (32.7, -89.7),
    "MO": (38.5, -92.5), "MT": (47.0, -110.0), "NE": (41.5, -99.9),
    "NV": (39.5, -117.1), "NH": (43.7, -71.6), "NJ": (40.1, -74.5),
    "NM": (34.8, -106.2), "NY": (42.2, -74.9), "NC": (35.6, -79.8),
    "ND": (47.5, -100.5), "OH": (40.4, -82.8), "OK": (35.6, -96.9),
    "OR": (44.6, -122.1), "PA": (40.6, -77.2), "RI": (41.7, -71.5),
    "SC": (33.9, -80.9), "SD": (44.4, -100.2), "TN": (35.9, -86.7),
    "TX": (31.5, -99.3), "UT": (39.4, -111.1), "VT": (44.0, -72.7),
    "VA": (37.8, -78.2), "WA": (47.4, -120.6), "WV": (38.6, -80.6),
    "WI": (44.3, -89.6), "WY": (43.0, -107.6),
}
