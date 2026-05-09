# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
streamlit_app/config.py — Shared constants for the Streamlit dashboard.
"""

import os

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

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

FEATURE_DISPLAY_LABELS = {
    # Top-10 most common in SHAP top-5
    "np_escalating_rank":                    "Escalating peer rank (YoY)",
    "np_spend_outlier_2023_real":            "Spend outlier vs peers (2023)",
    "np_spend_outlier_2023":                 "Spend outlier vs peers (2023)",
    "engagement_priority_score_real":        "Engagement priority score",
    "engagement_priority_score":             "Engagement priority score",
    "spend_trend_real":                      "Multi-year spend trend",
    "np_outlier_years_count_real":           "Years flagged as outlier vs peers",
    "np_outlier_years_count":                "Years flagged as outlier vs peers",
    "np_spend_vs_peer_avg_2024":             "2024 spend vs peer average",
    "np_spend_vs_peer_avg_2024_real":        "2024 spend vs peer average",
    "np_spend_vs_peer_avg_2023_real":        "2023 spend vs peer average",
    "np_spend_vs_peer_avg_2023":             "2023 spend vs peer average",
    "np_spend_vs_peer_avg_2022_real":        "2022 spend vs peer average",
    "np_spend_pct_rank_specialty_2024_real": "2024 spend percentile (specialty)",
    "np_spend_pct_rank_specialty_2024":      "2024 spend percentile (specialty)",
    "np_spend_pct_rank_specialty_2023_real": "2023 spend percentile (specialty)",
    "np_spend_pct_rank_specialty_2023":      "2023 spend percentile (specialty)",
    "np_spend_pct_rank_specialty_2022_real": "2022 spend percentile (specialty)",
    "np_spend_pct_rank_specialty_2022":      "2022 spend percentile (specialty)",

    # Direct spend measures
    "spend_2024_raw":                        "2024 total spend",
    "spend_2023_raw":                        "2023 total spend",
    "spend_2022_raw":                        "2022 total spend",
    "spend_2024":                            "2024 total spend",
    "spend_2023":                            "2023 total spend",
    "spend_2022":                            "2022 total spend",
    "peak_year_spend":                       "Peak-year spend",

    # Spend growth
    "yoy_growth_2324":                       "Spend growth 2023→2024",
    "yoy_growth_2223":                       "Spend growth 2022→2023",

    # Spend composition
    "pct_food_beverage":                     "Food/beverage share of spend",
    "pct_speaking_fee":                      "Speaking fee share of spend",
    "pct_consulting":                        "Consulting share of spend",

    # Outlier patterns
    "np_spend_outlier_2022_real":            "Spend outlier vs peers (2022)",
    "np_spend_outlier_2022":                 "Spend outlier vs peers (2022)",
    "np_spend_outlier_2024_real":            "Spend outlier vs peers (2024)",
    "np_spend_outlier_2024":                 "Spend outlier vs peers (2024)",
    "np_persistent_outlier":                 "Outlier vs peers in 2+ years",
    "np_persistent_outlier_real":            "Outlier vs peers in 2+ years",

    # Share-of-wallet features (industry-wide, from mart_benchmark.sql)
    "sow_dominant_years_count":              "Years Nova was dominant payer",

    # Composite flags (from mart_benchmark.sql)
    "dual_outlier_flag":                     "Outlier vs Nova peers and industry (2024)",
    "triple_signal_flag":                    "All-signal outlier (Nova + industry + exclusive SOW)",
    "escalating_risk_flag":                  "Escalating rank and share of wallet",
    "chronic_risk_flag":                     "Outlier in 2+ years (chronic)",

    # Interaction and meals
    "total_meals":                           "Total meals",
    "avg_meal_cost":                         "Average meal cost",
    "total_interactions":                    "Total rep interactions",
    "unique_reps_interacted":                "Unique reps interacted",
    "interaction_frequency_score":           "Interaction frequency score",

    # Annual cap usage
    "annual_cap_pct_used_2022":              "2022 annual cap used (%)",
    "annual_cap_pct_used_2023":              "2023 annual cap used (%)",
    "annual_cap_pct_used_2024":              "2024 annual cap used (%)",
}

# Keep old name as alias so existing imports don't break
SHAP_LABELS = FEATURE_DISPLAY_LABELS


def clean_feature_name(feature: str) -> str:
    """Display-label fallback for features not in FEATURE_DISPLAY_LABELS."""
    name = feature
    for suffix in ("_real", "_raw"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.startswith("np_"):
        name = "peer " + name[3:]
    return name.replace("_", " ").title()


# Plain-English explanations shown in SHAP bar chart tooltips.
# Keys are canonical feature names (without _real/_raw suffix).
# Lookup via get_feature_tooltip() which handles suffix stripping.
FEATURE_DISPLAY_TOOLTIPS: dict[str, str] = {
    # Peer-relative rank and outlier patterns
    "np_escalating_rank":               "Rank among same-specialty peers is rising year-over-year — this HCP is receiving a growing share of Nova's spend relative to peers.",
    "np_spend_outlier_2022":            "Statistical outlier vs same-specialty peers in 2022.",
    "np_spend_outlier_2023":            "Statistical outlier vs same-specialty peers in 2023.",
    "np_spend_outlier_2024":            "Statistical outlier vs same-specialty peers in 2024.",
    "np_outlier_years_count":           "Number of years (out of 2022–2024) this HCP was flagged as a statistical outlier vs same-specialty peers.",
    "np_persistent_outlier":            "Flagged as a statistical outlier vs same-specialty peers in two or more years — pattern is not isolated to a single year.",
    "np_spend_vs_peer_avg_2022":        "Difference between this HCP's spend and the same-specialty peer average in 2022.",
    "np_spend_vs_peer_avg_2023":        "Difference between this HCP's spend and the same-specialty peer average in 2023.",
    "np_spend_vs_peer_avg_2024":        "Difference between this HCP's spend and the same-specialty peer average in 2024.",
    "np_spend_pct_rank_specialty_2022": "Percentile rank among same-specialty HCPs by spend in 2022. 0.99 = top 1% of their specialty.",
    "np_spend_pct_rank_specialty_2023": "Percentile rank among same-specialty HCPs by spend in 2023. 0.99 = top 1% of their specialty.",
    "np_spend_pct_rank_specialty_2024": "Percentile rank among same-specialty HCPs by spend in 2024. 0.99 = top 1% of their specialty.",

    # Engagement
    "engagement_priority_score":        "Internal engagement prioritization score. High values indicate the HCP was flagged for elevated commercial attention.",

    # Direct spend
    "spend_trend":                      "Direction and magnitude of spend change across all available years.",
    "spend_2022":                       "Total spend received from Nova in 2022 across all transfer-of-value categories.",
    "spend_2023":                       "Total spend received from Nova in 2023 across all transfer-of-value categories.",
    "spend_2024":                       "Total spend received from Nova in 2024 across all transfer-of-value categories.",
    "peak_year_spend":                  "Highest single-year spend across all years on record.",

    # Spend growth
    "yoy_growth_2223":                  "Year-over-year spend change from 2022 to 2023.",
    "yoy_growth_2324":                  "Year-over-year spend change from 2023 to 2024.",

    # Spend composition
    "pct_food_beverage":                "Proportion of total spend from food and beverage (meals). High values may indicate per-event cap exposure.",
    "pct_speaking_fee":                 "Proportion of total spend from speaking fees. High values flag FMV and repeat-speaker risk.",
    "pct_consulting":                   "Proportion of total spend from consulting arrangements.",

    # Share-of-wallet
    "sow_dominant_years_count":         "Number of years Nova was the dominant pharma payer for this HCP across all industry transfers.",

    # Composite risk flags
    "dual_outlier_flag":                "Outlier both vs Nova's same-specialty peers and vs the broader pharma industry in 2024.",
    "triple_signal_flag":               "Highest-risk composite: Nova-peer outlier, industry outlier, and Nova was the dominant exclusive payer.",
    "escalating_risk_flag":             "Peer rank rising year-over-year AND Nova's share of their total pharma spend is increasing.",
    "chronic_risk_flag":                "Statistical outlier in two or more years — risk pattern is not isolated to a single year.",

    # Interactions and meals
    "total_meals":                      "Total number of meal events with Nova reps across all years.",
    "avg_meal_cost":                    "Average cost per meal event. High values may signal per-event cap exposure.",
    "total_interactions":               "Total number of rep interactions (all types) across all years.",
    "unique_reps_interacted":           "Number of distinct Nova reps who interacted with this HCP. High values can indicate rep-hopping patterns.",
    "interaction_frequency_score":      "Composite score capturing how frequently and recently this HCP interacted with Nova reps.",

    # Annual cap usage
    "annual_cap_pct_used_2022":         "Percentage of the annual meal/entertainment cap consumed in 2022. Values at or above 1.0 indicate cap breach.",
    "annual_cap_pct_used_2023":         "Percentage of the annual meal/entertainment cap consumed in 2023. Values at or above 1.0 indicate cap breach.",
    "annual_cap_pct_used_2024":         "Percentage of the annual meal/entertainment cap consumed in 2024. Values at or above 1.0 indicate cap breach.",
}


def get_feature_tooltip(feature: str) -> str:
    """Return plain-English tooltip for a SHAP feature, or empty string if unknown."""
    if feature in FEATURE_DISPLAY_TOOLTIPS:
        return FEATURE_DISPLAY_TOOLTIPS[feature]
    # Strip _real / _raw suffix and retry
    for suffix in ("_real", "_raw"):
        if feature.endswith(suffix):
            stripped = feature[: -len(suffix)]
            if stripped in FEATURE_DISPLAY_TOOLTIPS:
                return FEATURE_DISPLAY_TOOLTIPS[stripped]
    return ""

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
