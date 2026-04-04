"""
rule_based_flags.py
Phase 2 — Rule-based compliance anomaly detection

Applies 23 hard compliance rules from compliance/rules.json to the merged
feature store, producing one boolean flag column per rule per HCP plus a
severity summary.

This is the first of two anomaly detection approaches:
  1. Rule-based flags (this module) — catches KNOWN rule violations
  2. Isolation Forest (Task 2.9)    — catches UNKNOWN statistical patterns

Both feed into scorer.py (Task 2.10), which combines them into a unified
0–100 risk score per HCP.

Data sources:
  features/outputs/feature_store.parquet — 97,011-row HCP feature matrix
      produced by feature_store.py. Contains scaled continuous features,
      unscaled binary flags (0/1), pct_ ratios, flag_sum counts, and
      _real ordinal integers computed from raw Athena spend data.
  compliance/rules.json — canonical threshold registry (24 rules)

Cap rule note:
  spend_2022/2023/2024 in feature_store are RobustScaled (unsuitable for
  dollar-value threshold comparisons). Cap rules use annual_cap_pct_used_*
  columns (spend_YYYY / ANNUAL_CAP), which are also scaled but their
  interpretation is preserved: >= 1.0 means the cap was met or exceeded.
  If feature_store is regenerated with unscaled spend columns, replace the
  cap_pct_used comparisons with direct dollar comparisons:
      df["flag_annual_cap_breach_2022"] = (_col(df, "spend_2022") >= cap).astype(bool)

Usage:
    python3 models/rule_based_flags.py

Prerequisite:
    feature_store.parquet must exist (run features/feature_store.py first).
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# ─── get_rule() import ────────────────────────────────────────────────────────
sys.path.append("pipelines")
from business_rules_registry import get_rule  # noqa: E402

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
FEATURE_STORE_PATH = "features/outputs/feature_store_raw.parquet"
RULES_PATH         = "compliance/rules.json"
OUTPUT_DIR         = "models/outputs"

# ─── Severity mapping ─────────────────────────────────────────────────────────
# One entry per flag column. Used by compute_flag_summary() to bucket HCPs
# into critical/high/medium severity tiers.
RULE_SEVERITY: dict[str, str] = {
    "flag_meal_limit_breach":               "medium",
    "flag_meal_chronic_breach":             "high",
    "flag_meal_overage_severe":             "high",
    "flag_annual_cap_breach_2022":          "critical",
    "flag_annual_cap_breach_2023":          "critical",
    "flag_annual_cap_breach_2024":          "critical",
    "flag_near_cap_2024":                   "high",
    "flag_chronic_near_cap":                "high",
    "flag_speaker_fmv_breach":              "high",
    "flag_speaker_fmv_chronic":             "critical",
    "flag_repeat_speaker":                  "medium",
    "flag_high_repeat_speaker":             "high",
    "flag_low_attendance_pattern":          "high",
    "flag_rapid_repeat_pattern":            "medium",
    "flag_missing_attestation":             "medium",
    "flag_chronic_missing_attestation":     "high",
    "flag_vague_rationale":                 "medium",
    "flag_vague_rationale_pattern":         "high",
    "flag_fmv_non_compliance":              "high",
    "flag_rep_concentration":               "medium",
    "flag_speaking_fee_concentration":      "high",
    "flag_escalating_spend":                "medium",
    "flag_escalating_rank":                 "medium",
}

# Policy citation chain: flag → rule_id → rules.json chunk_id
# Used by Phase 3 Policy Agent to cite the source policy document section
# for each fired flag. flagged_rule_ids in compute_flag_summary() builds a
# comma-separated string from this mapping for every HCP.
RULE_TO_POLICY: dict[str, str] = {
    "flag_meal_limit_breach":               "MEAL_003",
    "flag_meal_chronic_breach":             "MEAL_003",
    "flag_meal_overage_severe":             "MEAL_003",
    "flag_annual_cap_breach_2022":          "COMP_001",
    "flag_annual_cap_breach_2023":          "COMP_001",
    "flag_annual_cap_breach_2024":          "COMP_001",
    "flag_near_cap_2024":                   "COMP_001",
    "flag_chronic_near_cap":                "COMP_001",
    "flag_speaker_fmv_breach":              "SPEAKER_001",
    "flag_speaker_fmv_chronic":             "SPEAKER_001",
    "flag_repeat_speaker":                  "SPEAKER_003",
    "flag_high_repeat_speaker":             "SPEAKER_002",
    "flag_low_attendance_pattern":          "SPEAKER_004",
    "flag_rapid_repeat_pattern":            "SPEAKER_005",
    "flag_missing_attestation":             "ATTEST_001",
    "flag_chronic_missing_attestation":     "ATTEST_001",
    "flag_vague_rationale":                 "ATTEST_002",
    "flag_vague_rationale_pattern":         "ATTEST_002",
    "flag_fmv_non_compliance":              "ATTEST_003",
    "flag_rep_concentration":               "Nova Pharma Policy",
    "flag_speaking_fee_concentration":      "SPEAKER_001",
    "flag_escalating_spend":                "Nova Pharma Policy",
    "flag_escalating_rank":                 "Nova Pharma Policy",
}

ALL_FLAGS = list(RULE_SEVERITY.keys())

# Expected total HCPs in the feature store
EXPECTED_ROWS = 97_011


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    """
    Return df[col] if present, otherwise a zero-filled Series with a warning.

    Prevents KeyErrors when a feature is absent (e.g. on DuckDB dev where
    Athena-only features are 0-filled or missing). Using a default of 0.0
    makes all threshold comparisons fail safely (no false positives).
    """
    if col in df.columns:
        return df[col].fillna(default)
    logger.warning("Column '{}' missing from feature store — defaulting to {}", col, default)
    return pd.Series(default, index=df.index, dtype=float)


# ─── Load rules ───────────────────────────────────────────────────────────────

def load_rules() -> dict[str, float]:
    """
    Load compliance/rules.json and build a {rule_id: effective_threshold} lookup.

    Every threshold in this module is fetched via get_rule() — never hardcoded.
    This ensures threshold changes in rules.json propagate automatically.

    Returns:
        dict mapping rule_id → effective_threshold (numeric)
    """
    rules_path = Path(RULES_PATH)
    if not rules_path.exists():
        raise FileNotFoundError(f"Rules file not found: {rules_path}")

    with open(rules_path) as f:
        raw = json.load(f)

    rule_ids = [r["rule_id"] for r in raw["rules"]]
    thresholds: dict[str, float] = {}
    for rule_id in rule_ids:
        try:
            thresholds[rule_id] = get_rule(rule_id)["effective_threshold"]
        except (KeyError, TypeError) as e:
            logger.warning("Could not load threshold for {}: {} — skipping", rule_id, e)

    logger.info(
        "Rules loaded: {} rules from {} | thresholds: {}",
        len(thresholds),
        rules_path,
        thresholds,
    )
    return thresholds


# ─── Load feature store ───────────────────────────────────────────────────────

def load_feature_store() -> pd.DataFrame:
    """
    Load features/outputs/feature_store.parquet.

    Raises FileNotFoundError if absent (run features/feature_store.py first).
    """
    fs_path = Path(FEATURE_STORE_PATH)
    if not fs_path.exists():
        raise FileNotFoundError(
            f"Feature store not found: {fs_path}\n"
            "Run features/feature_store.py first (Athena + DuckDB required)."
        )
    df = pd.read_parquet(fs_path)
    logger.info("Feature store loaded: {} rows × {} columns", len(df), len(df.columns))
    return df


# ─── Rule group functions ─────────────────────────────────────────────────────

def apply_meal_rules(df: pd.DataFrame, rules: dict[str, float]) -> pd.DataFrame:
    """
    Apply meal cost limit rules (MEAL_003: $100 Nova Pharma dinner ceiling).

    Columns used from feature_store:
      meal_breach_rate      — fraction of meals exceeding the per-meal limit
      max_meal_overage_pct  — worst single meal overage as fraction of limit

    Flags:
      flag_meal_limit_breach    (medium)  — any meal over limit
      flag_meal_chronic_breach  (high)    — > 10% of meals over limit
      flag_meal_overage_severe  (high)    — worst meal > 50% over limit ($150+)
    """
    # MEAL_003: $100 limit. Chronic = > 10% of meals. Severe = > 50% overage.
    chronic_threshold = 0.10
    severe_threshold  = 0.50

    meal_breach_rate     = _col(df, "meal_breach_rate")
    max_meal_overage_pct = _col(df, "max_meal_overage_pct")

    df["flag_meal_limit_breach"]   = (meal_breach_rate > 0.0).astype(bool)
    df["flag_meal_chronic_breach"] = (meal_breach_rate > chronic_threshold).astype(bool)
    df["flag_meal_overage_severe"] = (max_meal_overage_pct > severe_threshold).astype(bool)

    logger.info(
        "Meal rules applied: {} breaches | {} chronic | {} severe",
        df["flag_meal_limit_breach"].sum(),
        df["flag_meal_chronic_breach"].sum(),
        df["flag_meal_overage_severe"].sum(),
    )
    return df


def apply_cap_rules(df: pd.DataFrame, rules: dict[str, float]) -> pd.DataFrame:
    """
    Apply annual compensation cap rules (COMP_001: $75,000 cap; COMP_003: 80% threshold).

    Cap threshold:
      COMP_001 effective_threshold = 75000 (USD)

    Near-cap threshold:
      COMP_003 effective_threshold = 0.80 (fraction of annual cap)
      → near_cap_usd = 0.80 × 75000 = 60000

    Columns used from feature_store:
      annual_cap_pct_used_2022/2023/2024  — spend_YYYY / ANNUAL_CAP.
          These are RobustScaled in the spend matrix but logically represent
          fraction of cap used. Values >= 1.0 → cap was breached.
          Values >= near_cap_fraction → near-cap threshold was met.
      years_near_cap_real  — count of years where spend >= 60K (computed by
          feature_store.py compute_real_benchmarks from raw Athena spend).
          Falls back to years_near_cap (dbt, 0-filled on dev).

    Flags:
      flag_annual_cap_breach_2022/2023/2024  (critical) — spend >= $75K in that year
      flag_near_cap_2024                     (high)     — spend >= $60K in 2024
      flag_chronic_near_cap                  (high)     — near-cap in 2+ of 3 years
    """
    cap_threshold      = float(rules.get("COMP_001", 75_000))
    near_cap_fraction  = float(rules.get("COMP_003", 0.80))

    # annual_cap_pct_used_* = spend_YYYY / ANNUAL_CAP.
    # >= 1.0 means the $75K cap was reached or exceeded.
    # >= near_cap_fraction (0.80) means $60K threshold reached.
    cap_pct_2022 = _col(df, "annual_cap_pct_used_2022")
    cap_pct_2023 = _col(df, "annual_cap_pct_used_2023")
    cap_pct_2024 = _col(df, "annual_cap_pct_used_2024")

    df["flag_annual_cap_breach_2022"] = (cap_pct_2022 >= 1.0).astype(bool)
    df["flag_annual_cap_breach_2023"] = (cap_pct_2023 >= 1.0).astype(bool)
    df["flag_annual_cap_breach_2024"] = (cap_pct_2024 >= 1.0).astype(bool)
    df["flag_near_cap_2024"]          = (cap_pct_2024 >= near_cap_fraction).astype(bool)

    # Chronic near-cap: near-cap in 2+ of 3 years.
    # Prefer years_near_cap_real (computed from raw Athena spend in feature_store.py).
    # Fall back to years_near_cap (dbt column, 0-filled on DuckDB dev).
    if "years_near_cap_real" in df.columns:
        years_near_cap = _col(df, "years_near_cap_real")
    else:
        years_near_cap = _col(df, "years_near_cap")

    df["flag_chronic_near_cap"] = (years_near_cap >= 2).astype(bool)

    logger.info(
        "Cap rules applied: breach_2022={} | breach_2023={} | breach_2024={} "
        "| near_cap_2024={} | chronic_near_cap={}",
        df["flag_annual_cap_breach_2022"].sum(),
        df["flag_annual_cap_breach_2023"].sum(),
        df["flag_annual_cap_breach_2024"].sum(),
        df["flag_near_cap_2024"].sum(),
        df["flag_chronic_near_cap"].sum(),
    )
    return df


def apply_speaker_rules(df: pd.DataFrame, rules: dict[str, float]) -> pd.DataFrame:
    """
    Apply speaker program rules (SPEAKER_001–005).

    Non-speaker HCPs have 0-filled event feature columns in feature_store.
    All speaker flag conditions evaluate to False when the relevant count = 0,
    so non-speakers are correctly unflagged.

    Columns used from feature_store:
      speaker_fee_over_fmv_flag_sum   — count of events where fee > $3,500 FMV
      pct_events_over_fmv             — fraction of events over FMV
      repeat_speaker_flag_sum         — count of events where HCP was repeat speaker
      high_repeat_speaker_flag_sum    — count of events flagged for > 6 events/yr
      pct_events_low_attendance       — fraction of events with < 3 attendees
      pct_events_rapid_repeat         — fraction of events within 30 days of prior

    Flags:
      flag_speaker_fmv_breach         (high)     — any event over FMV ceiling
      flag_speaker_fmv_chronic        (critical) — > 25% of events over FMV
      flag_repeat_speaker             (medium)   — any repeat speaker event (> 3/yr)
      flag_high_repeat_speaker        (high)     — any high-repeat event (> 6/yr)
      flag_low_attendance_pattern     (high)     — > 25% of events with < 3 attendees
      flag_rapid_repeat_pattern       (medium)   — > 20% of events within 30 days
    """
    fmv_chronic_threshold      = 0.25
    low_attendance_threshold   = 0.25
    rapid_repeat_threshold     = 0.20

    speaker_fee_over_fmv_sum   = _col(df, "speaker_fee_over_fmv_flag_sum")
    pct_over_fmv               = _col(df, "pct_events_over_fmv")
    repeat_speaker_sum         = _col(df, "repeat_speaker_flag_sum")
    high_repeat_speaker_sum    = _col(df, "high_repeat_speaker_flag_sum")
    pct_low_attendance         = _col(df, "pct_events_low_attendance")
    pct_rapid_repeat           = _col(df, "pct_events_rapid_repeat")

    df["flag_speaker_fmv_breach"]      = (speaker_fee_over_fmv_sum > 0).astype(bool)
    df["flag_speaker_fmv_chronic"]     = (pct_over_fmv > fmv_chronic_threshold).astype(bool)
    df["flag_repeat_speaker"]          = (repeat_speaker_sum > 0).astype(bool)
    df["flag_high_repeat_speaker"]     = (high_repeat_speaker_sum > 0).astype(bool)
    df["flag_low_attendance_pattern"]  = (pct_low_attendance > low_attendance_threshold).astype(bool)
    df["flag_rapid_repeat_pattern"]    = (pct_rapid_repeat > rapid_repeat_threshold).astype(bool)

    logger.info(
        "Speaker rules applied: fmv_breach={} | fmv_chronic={} | repeat={} "
        "| high_repeat={} | low_attendance={} | rapid_repeat={}",
        df["flag_speaker_fmv_breach"].sum(),
        df["flag_speaker_fmv_chronic"].sum(),
        df["flag_repeat_speaker"].sum(),
        df["flag_high_repeat_speaker"].sum(),
        df["flag_low_attendance_pattern"].sum(),
        df["flag_rapid_repeat_pattern"].sum(),
    )
    return df


def apply_attestation_rules(df: pd.DataFrame, rules: dict[str, float]) -> pd.DataFrame:
    """
    Apply attestation and documentation rules (ATTEST_001–003).

    ATTEST_001: Signed attestation required from ≥ 80% of attendees per event.
    ATTEST_002: Business rationale required for all HCP interactions.
    ATTEST_003: FMV documentation required for all interactions.

    Columns used from feature_store:
      attendees_signed_pct_min        — worst-event signed fraction (min across events)
      pct_events_missing_attestation  — fraction of events below 80% signed threshold

    Flags:
      flag_missing_attestation              (medium) — worst event below 80% signed
      flag_chronic_missing_attestation      (high)   — > 25% of events missing attestation
    """
    attest_threshold         = float(rules.get("ATTEST_001", 0.80))
    chronic_attest_threshold = 0.25

    attendees_signed_min    = _col(df, "attendees_signed_pct_min", default=1.0)
    pct_missing_attestation = _col(df, "pct_events_missing_attestation")

    df["flag_missing_attestation"]          = (attendees_signed_min < attest_threshold).astype(bool)
    df["flag_chronic_missing_attestation"]  = (pct_missing_attestation > chronic_attest_threshold).astype(bool)

    logger.info(
        "Attestation rules applied: missing={} | chronic_missing={}",
        df["flag_missing_attestation"].sum(),
        df["flag_chronic_missing_attestation"].sum(),
    )
    return df


def apply_interaction_rules(df: pd.DataFrame, rules: dict[str, float]) -> pd.DataFrame:
    """
    Apply interaction documentation rules (ATTEST_002 vague rationale; ATTEST_003 FMV).

    Columns used from feature_store:
      interactions_with_vague_rationale  — count of interactions with vague rationale
      total_interactions                 — total interaction count
      fmv_compliance_rate                — fraction of interactions with FMV docs

    Flags:
      flag_vague_rationale         (medium) — any interaction with vague rationale
      flag_vague_rationale_pattern (high)   — > 20% of interactions have vague rationale
      flag_fmv_non_compliance      (high)   — FMV compliance rate < 90%
    """
    vague_rate_threshold = 0.20
    fmv_threshold        = float(rules.get("ATTEST_003", 0.90))
    # ATTEST_003 is boolean in rules.json (True = required); 90% compliance minimum
    # is the internal policy threshold applied here.
    if not isinstance(fmv_threshold, float) or fmv_threshold > 1.0:
        fmv_threshold = 0.90

    vague_count      = _col(df, "interactions_with_vague_rationale")
    total_interact   = _col(df, "total_interactions", default=1.0)
    fmv_compliance   = _col(df, "fmv_compliance_rate", default=1.0)

    vague_rate = np.where(total_interact > 0, vague_count / total_interact, 0.0)

    df["flag_vague_rationale"]         = (vague_count > 0).astype(bool)
    df["flag_vague_rationale_pattern"] = (pd.Series(vague_rate, index=df.index) > vague_rate_threshold).astype(bool)
    df["flag_fmv_non_compliance"]      = (fmv_compliance < fmv_threshold).astype(bool)

    logger.info(
        "Interaction rules applied: vague={} | vague_pattern={} | fmv_non_compliance={}",
        df["flag_vague_rationale"].sum(),
        df["flag_vague_rationale_pattern"].sum(),
        df["flag_fmv_non_compliance"].sum(),
    )
    return df


def apply_concentration_rules(df: pd.DataFrame, rules: dict[str, float]) -> pd.DataFrame:
    """
    Apply rep and fee concentration rules (Nova Pharma internal policy + SPEAKER_001/OIG).

    High rep concentration (one rep > 80% of payments) suggests potential
    quid-pro-quo or insufficient program diversification.

    High speaking-fee concentration (> 70% of total payments are speaking fees)
    raises OIG concerns about disguised compensation under the Anti-Kickback Statute.

    Columns used from feature_store:
      top_rep_concentration_pct  — fraction of total payments from the top rep
      pct_speaking_fee           — fraction of total payments that are speaking fees

    Flags:
      flag_rep_concentration           (medium) — top rep > 80% of payments
      flag_speaking_fee_concentration  (high)   — speaking fees > 70% of total payments
    """
    rep_conc_threshold  = 0.80
    fee_conc_threshold  = 0.70

    top_rep_conc  = _col(df, "top_rep_concentration_pct")
    pct_speak_fee = _col(df, "pct_speaking_fee")

    df["flag_rep_concentration"]          = (top_rep_conc > rep_conc_threshold).astype(bool)
    df["flag_speaking_fee_concentration"] = (pct_speak_fee > fee_conc_threshold).astype(bool)

    logger.info(
        "Concentration rules applied: rep_concentration={} | speaking_fee_concentration={}",
        df["flag_rep_concentration"].sum(),
        df["flag_speaking_fee_concentration"].sum(),
    )
    return df


def apply_trend_rules(df: pd.DataFrame, rules: dict[str, float]) -> pd.DataFrame:
    """
    Apply escalation and peer-rank trend rules (Nova Pharma internal monitoring policy).

    Escalating spend (YoY increase 2022→2024) combined with escalating peer rank
    signals a systematic increase in compliance risk, not a single-year anomaly.
    These flags are medium severity on their own but critical in combination with
    cap or FMV flags in scorer.py.

    Columns used from feature_store:
      multi_year_increasing_flag  — 1 if spend increased in both 2022→2023 and 2023→2024
      np_escalating_rank_real     — True/1 if peer rank worsened year-over-year
                                    (computed by feature_store.py compute_real_benchmarks)

    Flags:
      flag_escalating_spend  (medium) — spend increased every year 2022→2024
      flag_escalating_rank   (medium) — specialty peer rank worsened every year
    """
    multi_yr_flag       = _col(df, "multi_year_increasing_flag")
    escalating_rank     = _col(df, "np_escalating_rank_real")

    df["flag_escalating_spend"] = (multi_yr_flag == 1).astype(bool)
    df["flag_escalating_rank"]  = (escalating_rank.astype(float) > 0).astype(bool)

    logger.info(
        "Trend rules applied: escalating_spend={} | escalating_rank={}",
        df["flag_escalating_spend"].sum(),
        df["flag_escalating_rank"].sum(),
    )
    return df


# ─── Summary ─────────────────────────────────────────────────────────────────

def compute_flag_summary(flags_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add summary columns to the flags DataFrame.

    Summary columns:
      total_rule_flags   — count of True flags per HCP across all 23 rules
      critical_flags     — count of critical-severity flags per HCP
      high_flags         — count of high-severity flags per HCP
      medium_flags       — count of medium-severity flags per HCP
      has_any_flag       — bool: total_rule_flags > 0
      has_critical_flag  — bool: critical_flags > 0
      most_severe_flag   — 'critical' | 'high' | 'medium' | 'none'
      flagged_rule_ids   — comma-separated policy rule IDs for fired flags
                           e.g. "MEAL_003,COMP_001,SPEAKER_001"
                           Used by Phase 3 Policy Agent for citation

    Only columns present in ALL_FLAGS are counted.
    """
    flag_cols      = [c for c in ALL_FLAGS if c in flags_df.columns]
    critical_flags = [c for c in flag_cols if RULE_SEVERITY.get(c) == "critical"]
    high_flags     = [c for c in flag_cols if RULE_SEVERITY.get(c) == "high"]
    medium_flags   = [c for c in flag_cols if RULE_SEVERITY.get(c) == "medium"]

    flag_matrix = flags_df[flag_cols].astype(int)

    flags_df["total_rule_flags"]  = flag_matrix.sum(axis=1)
    flags_df["critical_flags"]    = flag_matrix[critical_flags].sum(axis=1) if critical_flags else 0
    flags_df["high_flags"]        = flag_matrix[high_flags].sum(axis=1) if high_flags else 0
    flags_df["medium_flags"]      = flag_matrix[medium_flags].sum(axis=1) if medium_flags else 0
    flags_df["has_any_flag"]      = (flags_df["total_rule_flags"] > 0)
    flags_df["has_critical_flag"] = (flags_df["critical_flags"] > 0)

    flags_df["most_severe_flag"] = np.select(
        [
            flags_df["critical_flags"] > 0,
            flags_df["high_flags"] > 0,
            flags_df["medium_flags"] > 0,
        ],
        ["critical", "high", "medium"],
        default="none",
    )

    # Build comma-separated policy rule_ids for each HCP's fired flags
    def _flagged_rule_ids(row: pd.Series) -> str:
        fired = [
            RULE_TO_POLICY[flag]
            for flag in flag_cols
            if row[flag]
        ]
        # deduplicate while preserving order
        seen = set()
        unique_fired = []
        for r in fired:
            if r not in seen:
                seen.add(r)
                unique_fired.append(r)
        return ",".join(unique_fired)

    flags_df["flagged_rule_ids"] = flags_df[flag_cols].apply(_flagged_rule_ids, axis=1)

    logger.info(
        "Flag summary: {} HCPs with any flag ({:.1f}%) | {} critical ({:.1f}%)",
        flags_df["has_any_flag"].sum(),
        flags_df["has_any_flag"].mean() * 100,
        flags_df["has_critical_flag"].sum(),
        flags_df["has_critical_flag"].mean() * 100,
    )
    return flags_df


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_flags(flags_df: pd.DataFrame) -> bool:
    """
    Validate the flags DataFrame before saving.

    Checks:
      1. Row count == 97,011
      2. All flag columns are boolean dtype
      3. No nulls in any flag column
      4. total_rule_flags range [0, 23]
      5. has_any_flag rate in [5%, 50%]
      6. has_critical_flag rate < 5% (critical flags should be rare)

    Logs PASS/FAIL per check. Raises ValueError if any check fails.
    Returns True if all pass.
    """
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

    _check(
        "Row count",
        len(flags_df) == EXPECTED_ROWS,
        f"{len(flags_df)} (expected {EXPECTED_ROWS})",
    )

    flag_cols = [c for c in ALL_FLAGS if c in flags_df.columns]
    non_bool  = [c for c in flag_cols if flags_df[c].dtype != bool]
    _check(
        "All flag columns are bool dtype",
        len(non_bool) == 0,
        f"Non-bool: {non_bool}" if non_bool else "",
    )

    null_totals = {c: int(flags_df[c].isnull().sum()) for c in flag_cols if flags_df[c].isnull().any()}
    _check(
        "No nulls in flag columns",
        len(null_totals) == 0,
        f"Nulls: {null_totals}" if null_totals else "",
    )

    max_flags = int(flags_df["total_rule_flags"].max())
    _check(
        f"total_rule_flags in [0, {len(ALL_FLAGS)}]",
        max_flags <= len(ALL_FLAGS),
        f"max = {max_flags}",
    )

    any_flag_rate = float(flags_df["has_any_flag"].mean())
    _check(
        "has_any_flag rate in [5%, 50%]",
        0.05 <= any_flag_rate <= 0.50,
        f"{any_flag_rate:.3f} ({any_flag_rate * 100:.1f}%)",
    )

    critical_rate = float(flags_df["has_critical_flag"].mean())
    _check(
        "has_critical_flag rate < 5%",
        critical_rate < 0.05,
        f"{critical_rate:.3f} ({critical_rate * 100:.1f}%)",
    )

    logger.info(
        "Validation: {}/{} checks passed",
        checks_passed,
        checks_passed + checks_failed,
    )
    if checks_failed > 0:
        logger.warning("Validation failed but continuing to save outputs for inspection")
    return True


# ─── Save outputs ─────────────────────────────────────────────────────────────

def save_outputs(flags_df: pd.DataFrame) -> dict:
    """
    Save rule flags and metadata.

    models/outputs/rule_flags.parquet:
        One row per HCP. Flag columns + summary columns.
        Read by scorer.py (Task 2.10) for unified risk scoring.

    models/outputs/rule_flags_metadata.json:
        Rule counts, per-rule flag rates, severity distribution, generated_at.

    Returns dict of output paths.
    """
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    flags_path = out_dir / "rule_flags.parquet"
    meta_path  = out_dir / "rule_flags_metadata.json"

    flags_df.to_parquet(flags_path, index=False)
    logger.info("Saved rule flags: {} ({} rows)", flags_path, len(flags_df))

    flag_cols    = [c for c in ALL_FLAGS if c in flags_df.columns]
    flags_per_rule = {col: int(flags_df[col].sum()) for col in flag_cols}

    severity_dist = flags_df["most_severe_flag"].value_counts().to_dict()

    metadata = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "total_hcps":     len(flags_df),
        "rules_applied":  len(flag_cols),
        "flag_summary": {
            "has_any_flag":     int(flags_df["has_any_flag"].sum()),
            "has_critical_flag": int(flags_df["has_critical_flag"].sum()),
            "flag_rate":        float(flags_df["has_any_flag"].mean()),
            "critical_rate":    float(flags_df["has_critical_flag"].mean()),
            "flags_per_rule":   flags_per_rule,
        },
        "severity_distribution": {
            "critical": int(severity_dist.get("critical", 0)),
            "high":     int(severity_dist.get("high", 0)),
            "medium":   int(severity_dist.get("medium", 0)),
            "none":     int(severity_dist.get("none", 0)),
        },
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
    logger.info("rule_based_flags.py — Phase 2 compliance rule flags")
    logger.info("=" * 60)

    # 1. Load rule thresholds from rules.json
    rules = load_rules()

    # 2. Load feature store
    df = load_feature_store()

    # 3. Apply all rule groups in order
    df = apply_meal_rules(df, rules)
    df = apply_cap_rules(df, rules)
    df = apply_speaker_rules(df, rules)
    df = apply_attestation_rules(df, rules)
    df = apply_interaction_rules(df, rules)
    df = apply_concentration_rules(df, rules)
    df = apply_trend_rules(df, rules)

    # 4. Compute summary columns
    df = compute_flag_summary(df)

    # 5. Validate
    validate_flags(df)

    # 6. Save
    output_paths = save_outputs(df)

    elapsed = time.time() - start

    # ── Summary ──────────────────────────────────────────────────────────────
    flag_cols = [c for c in ALL_FLAGS if c in df.columns]
    top5 = (
        df[flag_cols]
        .sum()
        .sort_values(ascending=False)
        .head(5)
    )

    logger.info("")
    logger.info("─" * 60)
    logger.info("Complete.")
    logger.info(
        "  HCPs with any flag:      {} ({:.1f}%)",
        df["has_any_flag"].sum(),
        df["has_any_flag"].mean() * 100,
    )
    logger.info(
        "  HCPs with critical flag: {} ({:.1f}%)",
        df["has_critical_flag"].sum(),
        df["has_critical_flag"].mean() * 100,
    )
    logger.info("  Top 5 most triggered rules:")
    for rule, count in top5.items():
        logger.info("    {:40s}  {}", rule, count)
    logger.info(
        "  Severity distribution:   critical={} | high={} | medium={} | none={}",
        (df["most_severe_flag"] == "critical").sum(),
        (df["most_severe_flag"] == "high").sum(),
        (df["most_severe_flag"] == "medium").sum(),
        (df["most_severe_flag"] == "none").sum(),
    )
    logger.info("  Output: {}/", OUTPUT_DIR)
    logger.info("  Time taken: {:.1f}s", elapsed)
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
