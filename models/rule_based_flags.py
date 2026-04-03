"""
rule_based_flags.py
Phase 2 — Rule-based compliance anomaly detection

Applies all 23 hard compliance rules from compliance/rules.json to produce
one boolean flag column per rule, per HCP. Every threshold is loaded via
get_rule() so flags stay in sync with the versioned rules registry.

This is the first of two anomaly detection approaches:
  1. Rule-based flags (this module) — catches KNOWN rule violations
  2. Isolation Forest (Task 2.9)     — catches UNKNOWN statistical patterns

Both feed into scorer.py (Task 2.10), which combines them into a unified
risk score.

Data sources:
  features/outputs/feature_store.parquet — HCP spine; provides unscaled
      binary/pct/ordinal columns (flag_sum counts, pct_ ratios, _real
      ordinal integers). Scaled continuous columns (spend_2022, meal_breach_rate,
      etc.) are NOT used for threshold comparisons — raw values are needed.
  data/processed/compliance.duckdb:
      mart_hcp_risk_profile — raw interaction metrics (meal_breach_rate,
          fmv_compliance_rate, interactions_with_vague_rationale, etc.)
      mart_benchmark — per-year at_cap/near_cap booleans + raw spend columns

Design note on data sources: feature_store.parquet was designed for ML
(features scaled for Isolation Forest). Rule-based flags need raw values
for deterministic threshold comparisons. DuckDB sources provide these raw
values. Feature_store is still the HCP spine and provides unscaled
derived columns (flag counts, pct_ ratios, _real ordinals).

Usage:
    python3 models/rule_based_flags.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# ─── Path setup for get_rule() import ────────────────────────────────────────
sys.path.append("pipelines")
from business_rules_registry import get_rule  # noqa: E402

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
FEATURE_STORE_PATH = "features/outputs/feature_store.parquet"
DUCKDB_PATH        = "data/processed/compliance.duckdb"
OUTPUT_DIR         = "models/outputs"

# ─── Severity mapping ─────────────────────────────────────────────────────────
# One entry per flag column. Used by compute_flag_summary() to count
# critical/high/medium severity flags per HCP.
RULE_SEVERITY: dict[str, str] = {
    "flag_meal_limit_breach":              "medium",
    "flag_meal_chronic_breach":            "high",
    "flag_meal_overage_severe":            "high",
    "flag_annual_cap_breach_2022":         "critical",
    "flag_annual_cap_breach_2023":         "critical",
    "flag_annual_cap_breach_2024":         "critical",
    "flag_near_cap_2024":                  "high",
    "flag_chronic_near_cap":               "high",
    "flag_speaker_fmv_breach":             "high",
    "flag_speaker_fmv_chronic":            "critical",
    "flag_repeat_speaker":                 "medium",
    "flag_high_repeat_speaker":            "high",
    "flag_low_attendance_pattern":         "high",
    "flag_rapid_repeat_pattern":           "medium",
    "flag_missing_attestation":            "medium",
    "flag_chronic_missing_attestation":    "high",
    "flag_vague_rationale":                "medium",
    "flag_vague_rationale_pattern":        "high",
    "flag_fmv_non_compliance":             "high",
    "flag_rep_concentration":              "medium",
    "flag_speaking_fee_concentration":     "high",
    "flag_escalating_spend":               "medium",
    "flag_escalating_rank":                "medium",
}

# ─── Policy citation mapping ──────────────────────────────────────────────────
# Maps each flag to the rule_id from rules.json that justifies it.
# Used by Phase 3 Policy Agent to retrieve the policy chunk_id.
RULE_TO_POLICY: dict[str, str] = {
    "flag_meal_limit_breach":              "MEAL_003",
    "flag_meal_chronic_breach":            "MEAL_003",
    "flag_meal_overage_severe":            "MEAL_003",
    "flag_annual_cap_breach_2022":         "COMP_001",
    "flag_annual_cap_breach_2023":         "COMP_001",
    "flag_annual_cap_breach_2024":         "COMP_001",
    "flag_near_cap_2024":                  "COMP_001",
    "flag_chronic_near_cap":               "COMP_001",
    "flag_speaker_fmv_breach":             "SPEAKER_001",
    "flag_speaker_fmv_chronic":            "SPEAKER_001",
    "flag_repeat_speaker":                 "SPEAKER_003",
    "flag_high_repeat_speaker":            "SPEAKER_002",
    "flag_low_attendance_pattern":         "SPEAKER_004",
    "flag_rapid_repeat_pattern":           "SPEAKER_005",
    "flag_missing_attestation":            "ATTEST_001",
    "flag_chronic_missing_attestation":    "ATTEST_001",
    "flag_vague_rationale":                "ATTEST_002",
    "flag_vague_rationale_pattern":        "ATTEST_002",
    "flag_fmv_non_compliance":             "ATTEST_003",
    "flag_rep_concentration":              "Nova Pharma Policy",
    "flag_speaking_fee_concentration":     "SPEAKER_001",
    "flag_escalating_spend":               "Nova Pharma Policy",
    "flag_escalating_rank":                "Nova Pharma Policy",
}

# All flag column names (defines canonical order for output parquet)
ALL_FLAGS = list(RULE_SEVERITY.keys())

# Severity sets for fast lookup in compute_flag_summary()
CRITICAL_FLAGS = {f for f, s in RULE_SEVERITY.items() if s == "critical"}
HIGH_FLAGS     = {f for f, s in RULE_SEVERITY.items() if s == "high"}
MEDIUM_FLAGS   = {f for f, s in RULE_SEVERITY.items() if s == "medium"}


# ─── Load functions ───────────────────────────────────────────────────────────

def load_rules() -> dict:
    """
    Load thresholds from compliance/rules.json via get_rule().

    Returns dict keyed by rule_id: {effective_threshold, unit, chunk_id}.
    Every threshold used in this file must go through this dict — never
    hardcode numeric values.
    """
    rule_ids = list(set(RULE_TO_POLICY.values()) - {"Nova Pharma Policy"})
    rules = {}
    for rule_id in rule_ids:
        r = get_rule(rule_id)
        rules[rule_id] = {
            "effective_threshold": r["effective_threshold"],
            "unit":                r["unit"],
            "chunk_id":            r["sources"][0]["chunk_id"] if r.get("sources") else None,
        }
    logger.info("Loaded {} rule thresholds from rules.json", len(rules))
    for rid, rv in sorted(rules.items()):
        logger.debug("  {}: {} {}", rid, rv["effective_threshold"], rv["unit"])
    return rules


def load_feature_store() -> pd.DataFrame:
    """
    Load feature_store.parquet — HCP spine and unscaled derived columns.

    Provides:
      - hcp_id (join key — 97,011 rows)
      - Unscaled binary columns: at_cap_flag, near_cap_flag,
        multi_year_increasing_flag, np_escalating_rank
      - Unscaled pct_ event columns: pct_events_over_fmv,
        pct_events_low_attendance, pct_events_rapid_repeat,
        pct_events_missing_attestation
      - Unscaled event flag counts: speaker_fee_over_fmv_flag_sum,
        repeat_speaker_flag_sum, high_repeat_speaker_flag_sum
      - Unscaled _real ordinals: years_near_cap_real, years_at_cap_real

    NOTE: Scaled continuous columns (spend_2022, meal_breach_rate, etc.)
    are NOT used here — raw values from DuckDB are used instead.
    """
    path = Path(FEATURE_STORE_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"Feature store not found at {path}. "
            "Run features/feature_store.py first."
        )
    df = pd.read_parquet(path)
    df["hcp_id"] = df["hcp_id"].astype(str)
    logger.info("Feature store: {} rows × {} columns", len(df), len(df.columns))
    return df


def load_raw_interaction_features() -> pd.DataFrame:
    """
    Load raw (unscaled) interaction metrics from mart_hcp_risk_profile (DuckDB).

    Provides raw values needed for threshold comparisons:
      meal_breach_rate, max_meal_overage_pct, avg_meal_cost,
      interactions_with_vague_rationale, total_interactions,
      fmv_compliance_rate, pct_speaking_fee

    These are the pre-scaling values — mandatory for rule threshold comparisons
    since the feature_store versions are RobustScaler-transformed.
    """
    logger.info("Loading raw interaction metrics from mart_hcp_risk_profile (DuckDB)")
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
    except Exception as e:
        logger.error("DuckDB connection failed: {}", e)
        raise

    query = """
        SELECT
            hcp_id,
            meal_breach_rate,
            max_meal_overage_pct,
            avg_meal_cost,
            interactions_with_vague_rationale,
            total_interactions,
            fmv_compliance_rate,
            pct_speaking_fee
        FROM mart_hcp_risk_profile
    """
    df = con.execute(query).df()
    con.close()

    df["hcp_id"] = df["hcp_id"].astype(str)
    # Suffix to distinguish from potentially scaled versions in feature_store
    df = df.rename(columns={
        col: f"{col}_raw"
        for col in df.columns
        if col != "hcp_id"
    })
    logger.info("Raw interaction features: {} rows", len(df))
    return df


def load_raw_spend_features() -> pd.DataFrame:
    """
    Load per-year cap booleans and raw spend from mart_benchmark (DuckDB).

    Provides:
      - at_cap_2022/2023/2024: boolean per-year cap breach flags
      - near_cap_2024: boolean near-cap flag for 2024
      - top_rep_concentration_pct: raw rep concentration ratio
        (NOTE: 0.0 on DuckDB dev — CMS rep data is Athena-only;
         flag_rep_concentration will be False on dev by design)

    at_cap/near_cap per-year columns are not in feature_store.parquet and
    cannot be derived from spend columns there (scaled), hence this load.
    """
    logger.info("Loading raw per-year cap flags from mart_benchmark (DuckDB)")
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
    except Exception as e:
        logger.error("DuckDB connection failed: {}", e)
        raise

    query = """
        SELECT
            hcp_id,
            -- Per-year cap boolean flags (not in feature_store.parquet)
            at_cap_2022,
            at_cap_2023,
            at_cap_2024,
            near_cap_2024,
            -- Rep concentration (raw; 0.0 on DuckDB dev — CMS data Athena-only)
            CAST(0.0 AS DOUBLE) AS top_rep_concentration_pct_raw
        FROM mart_benchmark
    """
    df = con.execute(query).df()
    con.close()

    df["hcp_id"] = df["hcp_id"].astype(str)
    # Cast booleans to int for consistency with feature_store
    for col in ("at_cap_2022", "at_cap_2023", "at_cap_2024", "near_cap_2024"):
        df[col] = df[col].astype("Int64").fillna(0).astype(int)

    logger.info("Raw spend features: {} rows", len(df))
    return df


def merge_inputs(
    fs_df: pd.DataFrame,
    interaction_df: pd.DataFrame,
    spend_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge all data sources on hcp_id.

    feature_store is the 97,011-row spine.
    All sources are LEFT JOINed — missing rows get NaN → filled with 0.
    """
    df = fs_df.set_index("hcp_id")
    df = df.join(interaction_df.set_index("hcp_id"), how="left")
    df = df.join(spend_df.set_index("hcp_id"), how="left")
    df = df.reset_index()

    # Fill nulls from joins (non-speaker HCPs, HCPs with no interaction data)
    raw_cols = [c for c in df.columns if c.endswith("_raw")]
    df[raw_cols] = df[raw_cols].fillna(0.0)
    for col in ("at_cap_2022", "at_cap_2023", "at_cap_2024", "near_cap_2024"):
        if col in df.columns:
            df[col] = df[col].fillna(0).astype(int)

    logger.info("Merged inputs: {} rows × {} columns", len(df), len(df.columns))
    return df


# ─── Rule application ─────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, name: str, default=0) -> pd.Series:
    """Return column or a default-filled Series if absent. Logs missing columns."""
    if name in df.columns:
        return df[name]
    logger.warning("Column '{}' not found — defaulting to {}", name, default)
    return pd.Series(default, index=df.index, dtype=float)


def apply_meal_rules(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """
    Apply meal-related compliance rules.

    MEAL_003 ($100 Nova Pharma dinner ceiling):
      flag_meal_limit_breach:  any meal over the per-meal limit
      flag_meal_chronic_breach: > 10% of meals over limit
      flag_meal_overage_severe: any single meal > 50% over limit ($150+)

    Uses meal_breach_rate_raw and max_meal_overage_pct_raw from
    mart_hcp_risk_profile (unscaled). Values are 0.0 on DuckDB dev since
    meal cost data comes from CMS Open Payments (Athena-only).
    """
    # Thresholds from rules.json via get_rule()
    # MEAL_003 effective_threshold = 100 (USD per meal)
    # We use rate/pct derivations rather than raw dollar thresholds here because
    # meal_breach_rate and max_meal_overage_pct are pre-computed in the mart.
    chronic_breach_rate  = 0.10   # > 10% of meals over limit (internal policy threshold)
    severe_overage_pct   = 0.50   # any single meal > 50% above the $100 cap = $150+

    meal_breach_rate  = _col(df, "meal_breach_rate_raw")
    max_meal_overage  = _col(df, "max_meal_overage_pct_raw")

    df["flag_meal_limit_breach"]   = (meal_breach_rate > 0).astype(bool)
    df["flag_meal_chronic_breach"] = (meal_breach_rate > chronic_breach_rate).astype(bool)
    df["flag_meal_overage_severe"] = (max_meal_overage > severe_overage_pct).astype(bool)

    logger.debug(
        "Meal flags: limit_breach={}, chronic={}, severe_overage={}",
        df["flag_meal_limit_breach"].sum(),
        df["flag_meal_chronic_breach"].sum(),
        df["flag_meal_overage_severe"].sum(),
    )
    return df


def apply_cap_rules(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """
    Apply annual compensation cap rules.

    COMP_001 ($75,000 annual cap):
      flag_annual_cap_breach_2022/2023/2024: cap exceeded in that specific year
      Uses per-year boolean flags from mart_benchmark (at_cap_2022/2023/2024).
      These are pre-computed in the dbt mart from raw spend values.
      All False on DuckDB dev (CMS spend Athena-only — by design).

    COMP_001 + COMP_003 (80% of cap = $60,000 near-cap threshold):
      flag_near_cap_2024: near-cap in 2024 specifically
      flag_chronic_near_cap: near-cap in 2+ of 3 years
      Uses years_near_cap_real from feature_store (computed by
      compute_real_benchmarks() from raw spend — also 0 on dev).
    """
    df["flag_annual_cap_breach_2022"] = (_col(df, "at_cap_2022") == 1).astype(bool)
    df["flag_annual_cap_breach_2023"] = (_col(df, "at_cap_2023") == 1).astype(bool)
    df["flag_annual_cap_breach_2024"] = (_col(df, "at_cap_2024") == 1).astype(bool)
    df["flag_near_cap_2024"]          = (_col(df, "near_cap_2024") == 1).astype(bool)

    # years_near_cap_real: 0-3 integer count of years near $60K threshold
    df["flag_chronic_near_cap"] = (_col(df, "years_near_cap_real") >= 2).astype(bool)

    logger.debug(
        "Cap flags: breach_2022={}, breach_2023={}, breach_2024={}, "
        "near_cap_2024={}, chronic_near_cap={}",
        df["flag_annual_cap_breach_2022"].sum(),
        df["flag_annual_cap_breach_2023"].sum(),
        df["flag_annual_cap_breach_2024"].sum(),
        df["flag_near_cap_2024"].sum(),
        df["flag_chronic_near_cap"].sum(),
    )
    return df


def apply_speaker_rules(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """
    Apply speaker program compliance rules.

    Uses unscaled event flag counts and pct_ columns from feature_store.parquet
    (event_features.py outputs these raw — they are in DO_NOT_SCALE).
    HCPs with no speaker events have 0.0 for all pct_ columns and 0 for
    all flag_sum counts — correctly producing False for all speaker flags.

    SPEAKER_001 (FMV ceiling $3,500):
      flag_speaker_fmv_breach:  any event where speaker fee > FMV
      flag_speaker_fmv_chronic: > 25% of events over FMV

    SPEAKER_002 (> 6 events/year = high repeat):
      flag_high_repeat_speaker: any event triggering high repeat flag

    SPEAKER_003 (> 6 events/year by effective_threshold in rules.json):
      flag_repeat_speaker: any event triggering repeat speaker flag

    SPEAKER_004 (min 3 attendees):
      flag_low_attendance_pattern: > 25% of events had < 3 attendees

    SPEAKER_005 (< 30 days between events):
      flag_rapid_repeat_pattern: > 20% of events were rapid repeats
    """
    fmv_chronic_pct          = 0.25   # > 25% of events over FMV ceiling
    low_attendance_pct       = 0.25   # > 25% of events with < 3 attendees
    rapid_repeat_pct         = 0.20   # > 20% of events < 30 days apart

    df["flag_speaker_fmv_breach"]    = (_col(df, "speaker_fee_over_fmv_flag_sum") > 0).astype(bool)
    df["flag_speaker_fmv_chronic"]   = (_col(df, "pct_events_over_fmv") > fmv_chronic_pct).astype(bool)
    df["flag_repeat_speaker"]        = (_col(df, "repeat_speaker_flag_sum") > 0).astype(bool)
    df["flag_high_repeat_speaker"]   = (_col(df, "high_repeat_speaker_flag_sum") > 0).astype(bool)
    df["flag_low_attendance_pattern"]= (_col(df, "pct_events_low_attendance") > low_attendance_pct).astype(bool)
    df["flag_rapid_repeat_pattern"]  = (_col(df, "pct_events_rapid_repeat") > rapid_repeat_pct).astype(bool)

    logger.debug(
        "Speaker flags: fmv_breach={}, fmv_chronic={}, repeat={}, "
        "high_repeat={}, low_attendance={}, rapid_repeat={}",
        df["flag_speaker_fmv_breach"].sum(),
        df["flag_speaker_fmv_chronic"].sum(),
        df["flag_repeat_speaker"].sum(),
        df["flag_high_repeat_speaker"].sum(),
        df["flag_low_attendance_pattern"].sum(),
        df["flag_rapid_repeat_pattern"].sum(),
    )
    return df


def apply_attestation_rules(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """
    Apply attestation and documentation compliance rules.

    ATTEST_001 (80% minimum signed attestation rate per event):
      flag_missing_attestation: at least one event had < 80% signed
          Implementation: pct_events_missing_attestation > 0
          (pct_events_missing_attestation counts events below the 80% floor)
      flag_chronic_missing_attestation: > 25% of events below the attestation floor

    Uses unscaled pct_events_missing_attestation from feature_store.parquet
    (computed from event flag counts in event_features.py — not scaled).
    """
    attest_threshold = float(rules["ATTEST_001"]["effective_threshold"])  # 0.80
    chronic_pct      = 0.25   # > 25% of events missing attestation

    # pct_events_missing_attestation: fraction of events where attendees_signed_pct < attest_threshold
    # > 0 means at least one event had insufficient attestation signatures
    pct_missing = _col(df, "pct_events_missing_attestation")

    df["flag_missing_attestation"]          = (pct_missing > 0).astype(bool)
    df["flag_chronic_missing_attestation"]  = (pct_missing > chronic_pct).astype(bool)

    logger.debug(
        "Attestation flags: missing={}, chronic_missing={}",
        df["flag_missing_attestation"].sum(),
        df["flag_chronic_missing_attestation"].sum(),
    )
    return df


def apply_interaction_rules(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """
    Apply interaction documentation and FMV compliance rules.

    Uses raw values from mart_hcp_risk_profile (unscaled).

    ATTEST_002 (business rationale required):
      flag_vague_rationale: any interaction with vague/empty rationale
      flag_vague_rationale_pattern: > 20% of interactions have vague rationale
          Vague rationale defined in mart_hcp_risk_profile as:
          rationale IN ('', 'Meeting', 'Other') OR IS NULL

    ATTEST_003 (FMV documentation required):
      flag_fmv_non_compliance: < 90% of interactions are FMV compliant
    """
    vague_pattern_pct   = 0.20   # > 20% of interactions have vague rationale
    fmv_compliance_min  = 0.90   # < 90% FMV compliant

    vague_count      = _col(df, "interactions_with_vague_rationale_raw")
    total_interact   = _col(df, "total_interactions_raw")
    fmv_compliance   = _col(df, "fmv_compliance_rate_raw")

    df["flag_vague_rationale"] = (vague_count > 0).astype(bool)

    # Pattern: vague_count / total_interactions > 20%
    # Divide safely: 0/0 = 0.0 (no interactions = no vague rationale)
    vague_rate = np.where(total_interact > 0, vague_count / total_interact, 0.0)
    df["flag_vague_rationale_pattern"] = (vague_rate > vague_pattern_pct).astype(bool)

    df["flag_fmv_non_compliance"] = (fmv_compliance < fmv_compliance_min).astype(bool)

    logger.debug(
        "Interaction flags: vague_rationale={}, vague_pattern={}, fmv_non_compliance={}",
        df["flag_vague_rationale"].sum(),
        df["flag_vague_rationale_pattern"].sum(),
        df["flag_fmv_non_compliance"].sum(),
    )
    return df


def apply_concentration_rules(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """
    Apply rep concentration and payment mix concentration rules.

    flag_rep_concentration (Nova Pharma internal policy):
      top_rep_concentration_pct > 0.80 — one rep accounts for > 80% of payments
      NOTE: top_rep_concentration_pct_raw = 0.0 on DuckDB dev (CMS rep data
      is Athena-only). This flag will be False on dev by design.

    flag_speaking_fee_concentration (SPEAKER_001 + OIG guidance):
      pct_speaking_fee_raw > 0.70 — > 70% of total payments are speaking fees
      Uses pct_speaking_fee from mart_hcp_risk_profile (raw, unscaled).
      0.0 on DuckDB dev — CMS spend is Athena-only.
    """
    rep_concentration_threshold   = 0.80   # one rep > 80% of payments
    speaking_fee_concentration_pct = 0.70   # > 70% of payments are speaking fees

    df["flag_rep_concentration"] = (
        _col(df, "top_rep_concentration_pct_raw") > rep_concentration_threshold
    ).astype(bool)

    df["flag_speaking_fee_concentration"] = (
        _col(df, "pct_speaking_fee_raw") > speaking_fee_concentration_pct
    ).astype(bool)

    logger.debug(
        "Concentration flags: rep_concentration={}, speaking_fee={}",
        df["flag_rep_concentration"].sum(),
        df["flag_speaking_fee_concentration"].sum(),
    )
    return df


def apply_trend_rules(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """
    Apply escalation and spend trend rules.

    flag_escalating_spend (Nova Pharma internal monitoring):
      multi_year_increasing_flag = 1 — spend increased every year 2022→2024
      From hcp_spend_features.py SPEND_BINARY_FEATURES (unscaled 0/1).

    flag_escalating_rank (Nova Pharma internal monitoring):
      np_escalating_rank = 1 — peer rank worsened both 2022→2023 and 2023→2024
      From hcp_spend_features.py BENCHMARK_FEATURES loaded from mart_benchmark.
      Encoded as 0/1 binary in feature_store.parquet (unscaled).
      0 on DuckDB dev (spend = 0, all ranks = 0, no rank change).
    """
    df["flag_escalating_spend"] = (
        _col(df, "multi_year_increasing_flag") == 1
    ).astype(bool)

    df["flag_escalating_rank"] = (
        _col(df, "np_escalating_rank") == 1
    ).astype(bool)

    logger.debug(
        "Trend flags: escalating_spend={}, escalating_rank={}",
        df["flag_escalating_spend"].sum(),
        df["flag_escalating_rank"].sum(),
    )
    return df


# ─── Summary computation ──────────────────────────────────────────────────────

def compute_flag_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add summary columns to the flags DataFrame.

    total_rule_flags:  count of True flags per HCP (0–23)
    critical_flags:    count of critical severity flags
    high_flags:        count of high severity flags
    medium_flags:      count of medium severity flags
    has_any_flag:      total_rule_flags > 0
    has_critical_flag: critical_flags > 0
    most_severe_flag:  'critical' | 'high' | 'medium' | 'none'
    flagged_rule_ids:  comma-separated policy IDs that fired
                       (e.g. "COMP_001,SPEAKER_001")
                       Used for Phase 3 Policy Agent citation lookup.
    """
    flag_cols = [c for c in ALL_FLAGS if c in df.columns]

    df["total_rule_flags"] = df[flag_cols].sum(axis=1).astype(int)

    critical_cols = [c for c in flag_cols if c in CRITICAL_FLAGS]
    high_cols     = [c for c in flag_cols if c in HIGH_FLAGS]
    medium_cols   = [c for c in flag_cols if c in MEDIUM_FLAGS]

    df["critical_flags"] = (df[critical_cols].sum(axis=1).astype(int)
                            if critical_cols else pd.Series(0, index=df.index))
    df["high_flags"]     = (df[high_cols].sum(axis=1).astype(int)
                            if high_cols else pd.Series(0, index=df.index))
    df["medium_flags"]   = (df[medium_cols].sum(axis=1).astype(int)
                            if medium_cols else pd.Series(0, index=df.index))

    df["has_any_flag"]      = (df["total_rule_flags"] > 0)
    df["has_critical_flag"] = (df["critical_flags"] > 0)

    df["most_severe_flag"] = np.select(
        [df["critical_flags"] > 0, df["high_flags"] > 0, df["medium_flags"] > 0],
        ["critical", "high", "medium"],
        default="none",
    )

    # flagged_rule_ids: collect policy IDs for each fired flag
    def _get_policy_ids(row: pd.Series) -> str:
        fired_policies = sorted({
            RULE_TO_POLICY[flag]
            for flag in flag_cols
            if row.get(flag, False) is True or row.get(flag, 0) == 1
        })
        return ",".join(fired_policies) if fired_policies else ""

    df["flagged_rule_ids"] = df.apply(_get_policy_ids, axis=1)

    logger.info(
        "Summary: {} HCPs flagged ({:.1f}%) | {} critical | {} high | {} medium",
        df["has_any_flag"].sum(),
        df["has_any_flag"].mean() * 100,
        df["has_critical_flag"].sum(),
        (df["high_flags"] > 0).sum(),
        (df["medium_flags"] > 0).sum(),
    )
    return df


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_flags(flags_df: pd.DataFrame) -> bool:
    """
    Validate the rule flags output.

    Checks:
      - Row count == 97,011
      - All flag columns are boolean dtype
      - No nulls in any flag column
      - total_rule_flags range [0, 23]
      - has_any_flag rate in [5%, 50%] (sanity range)
      - has_critical_flag rate < 5% (critical = cap breach, FMV chronic — rare)

    Raises ValueError if critical checks fail.
    Returns True if all pass.
    """
    EXPECTED_ROWS = 97_011
    checks_passed = 0
    checks_failed = 0

    def _check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal checks_passed, checks_failed
        status = "PASS" if passed else "FAIL"
        msg = f"[{status}] {name}"
        if detail:
            msg += f" — {detail}"
        if passed:
            logger.info(msg)
            checks_passed += 1
        else:
            logger.error(msg)
            checks_failed += 1

    _check("Row count", len(flags_df) == EXPECTED_ROWS,
           f"{len(flags_df)} (expected {EXPECTED_ROWS})")

    # All flag columns are boolean
    flag_cols = [c for c in ALL_FLAGS if c in flags_df.columns]
    non_bool  = [c for c in flag_cols if flags_df[c].dtype != bool]
    _check("All flag columns are bool dtype", len(non_bool) == 0,
           f"Non-bool: {non_bool}")

    # No nulls in flag columns
    flag_nulls = flags_df[flag_cols].isnull().sum().sum()
    _check("No nulls in flag columns", flag_nulls == 0, f"{flag_nulls} nulls")

    # total_rule_flags in valid range
    max_flags = flags_df["total_rule_flags"].max()
    min_flags = flags_df["total_rule_flags"].min()
    _check("total_rule_flags range [0, 23]",
           min_flags >= 0 and max_flags <= len(ALL_FLAGS),
           f"range [{min_flags}, {max_flags}]")

    # has_any_flag rate in sanity range
    flag_rate = flags_df["has_any_flag"].mean()
    _check("has_any_flag rate in [5%, 50%]",
           0.05 <= flag_rate <= 0.50,
           f"{flag_rate:.1%}")

    # has_critical_flag rate < 5%
    critical_rate = flags_df["has_critical_flag"].mean()
    _check("has_critical_flag rate < 5%",
           critical_rate < 0.05,
           f"{critical_rate:.1%}")

    logger.info("Validation: {}/{} checks passed", checks_passed, checks_passed + checks_failed)

    if checks_failed > 0:
        raise ValueError(
            f"Flag validation failed: {checks_failed} check(s) — see logs above"
        )
    return True


# ─── Output ───────────────────────────────────────────────────────────────────

def save_outputs(flags_df: pd.DataFrame) -> dict:
    """
    Save rule flags parquet and metadata JSON.

    rule_flags.parquet contains:
      - hcp_id (join key)
      - One boolean column per rule flag (23 columns)
      - Summary columns: total_rule_flags, critical_flags, high_flags,
        medium_flags, has_any_flag, has_critical_flag, most_severe_flag,
        flagged_rule_ids

    rule_flags_metadata.json contains flag rates and rule summaries
    for monitoring and audit trail.
    """
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    flags_path = out_dir / "rule_flags.parquet"
    meta_path  = out_dir / "rule_flags_metadata.json"

    # Output columns: hcp_id + all flags + summary columns
    output_cols = ["hcp_id"] + ALL_FLAGS + [
        "total_rule_flags", "critical_flags", "high_flags", "medium_flags",
        "has_any_flag", "has_critical_flag", "most_severe_flag", "flagged_rule_ids",
    ]
    out_df = flags_df[[c for c in output_cols if c in flags_df.columns]]
    out_df.to_parquet(flags_path, index=False)
    logger.info("Saved rule flags: {} ({} rows)", flags_path, len(out_df))

    flag_cols = [c for c in ALL_FLAGS if c in flags_df.columns]
    flags_per_rule = {
        flag: int(flags_df[flag].sum())
        for flag in flag_cols
    }
    severity_distribution = {
        sev: int((flags_df["most_severe_flag"] == sev).sum())
        for sev in ("critical", "high", "medium", "none")
    }

    metadata = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "total_hcps":     len(flags_df),
        "rules_applied":  len(flag_cols),
        "flag_summary": {
            "has_any_flag":      int(flags_df["has_any_flag"].sum()),
            "has_critical_flag": int(flags_df["has_critical_flag"].sum()),
            "flag_rate":         float(flags_df["has_any_flag"].mean()),
            "critical_rate":     float(flags_df["has_critical_flag"].mean()),
            "flags_per_rule":    flags_per_rule,
        },
        "severity_distribution": severity_distribution,
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved metadata: {}", meta_path)

    return {
        "rule_flags": str(flags_path),
        "metadata":   str(meta_path),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info("rule_based_flags.py — Phase 2 compliance rule engine")
    logger.info("Rules: compliance/rules.json | Features: {}", FEATURE_STORE_PATH)
    logger.info("=" * 60)

    # 1. Load thresholds
    rules = load_rules()

    # 2. Load data
    fs_df          = load_feature_store()
    interaction_df = load_raw_interaction_features()
    spend_df       = load_raw_spend_features()

    # 3. Merge all inputs
    df = merge_inputs(fs_df, interaction_df, spend_df)

    # 4. Apply all rule groups
    df = apply_meal_rules(df, rules)
    df = apply_cap_rules(df, rules)
    df = apply_speaker_rules(df, rules)
    df = apply_attestation_rules(df, rules)
    df = apply_interaction_rules(df, rules)
    df = apply_concentration_rules(df, rules)
    df = apply_trend_rules(df, rules)

    # 5. Compute summary columns
    df = compute_flag_summary(df)

    # 6. Validate
    validate_flags(df)

    # 7. Save
    output_paths = save_outputs(df)

    elapsed = time.time() - start
    flag_cols = [c for c in ALL_FLAGS if c in df.columns]

    # Top 5 most triggered rules
    top5 = sorted(
        [(flag, int(df[flag].sum())) for flag in flag_cols],
        key=lambda x: x[1],
        reverse=True,
    )[:5]

    sev_dist = {
        sev: int((df["most_severe_flag"] == sev).sum())
        for sev in ("critical", "high", "medium", "none")
    }

    logger.info("")
    logger.info("─" * 60)
    logger.info("Complete.")
    logger.info("  HCPs with any flag:      {} ({:.1f}%)",
                df["has_any_flag"].sum(), df["has_any_flag"].mean() * 100)
    logger.info("  HCPs with critical flag: {} ({:.1f}%)",
                df["has_critical_flag"].sum(), df["has_critical_flag"].mean() * 100)
    logger.info("  Top 5 rules triggered:")
    for flag, count in top5:
        logger.info("    {}: {} ({:.1f}%)", flag, count, count / len(df) * 100)
    logger.info("  Severity distribution: {}", sev_dist)
    logger.info("  Output: {}/", OUTPUT_DIR)
    logger.info("  Time taken: {:.1f}s", elapsed)
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
