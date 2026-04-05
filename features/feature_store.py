"""
feature_store.py
Phase 2 — Central feature store: merge, recompute, and validate all features

Merges all feature matrices into a single 97,011-row train-ready DataFrame
and resolves the Athena/DuckDB split that was a known limitation since
Tasks 2.3 and 2.4.

Data flow:
  1. hcp_spend_feature_matrix.parquet  (Athena — 97,011 rows, scaled)
  2. event_feature_matrix.parquet      (DuckDB — 1,354 speaker rows, scaled)
  3. mart_hcp_risk_profile             (DuckDB — 97,011 rows)
  4. mart_benchmark                    (DuckDB — 97,011 rows)
  → merge on hcp_id (LEFT JOIN from spend spine)
  → compute_real_benchmarks() using real Athena spend data
  → extract ground truth separately
  → build clean feature matrix (no strings, no GT)
  → feature_store.parquet + ground_truth_labels.parquet

Prerequisites:
  Run features/hcp_spend_features.py first (Athena required).
  Run features/event_features.py first (DuckDB only).

Usage:
    python3 features/feature_store.py
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# ─── Environment ─────────────────────────────────────────────────────────────
load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
DUCKDB_PATH  = "data/processed/compliance.duckdb"
OUTPUT_DIR   = "features/outputs"

SPEND_MATRIX_PATH = "features/outputs/hcp_spend_feature_matrix.parquet"
EVENT_MATRIX_PATH = "features/outputs/event_feature_matrix.parquet"

# Annual compensation cap (COMP_001 — compliance/rules.json)
ANNUAL_CAP = 75_000.0

# Cap / near-cap threshold (COMP_003 — compliance/rules.json)
NEAR_CAP_THRESHOLD = 60_000.0

# Ordinal encoding for cap_pattern_real (higher = more severe)
CAP_PATTERN_ORDINAL = {
    "compliant":        0,
    "near_cap":         1,
    "chronic_near_cap": 2,
    "single_breach":    3,
    "chronic_breach":   4,
}

# Ordinal encoding for spend_trend_real
SPEND_TREND_ORDINAL = {
    "decreasing":    0,
    "stable":        1,
    "net_increasing": 2,
    "increasing":    3,
}

# Columns excluded from the ML feature matrix.
# Identity, metadata, categorical string, and validation columns.
# _real benchmark string columns (cap_pattern_real, spend_trend_real) are
# encoded to integers before this list is applied.
EXCLUDE_FROM_FEATURES = [
    # Identity columns
    "hcp_id",
    "hcp_name",
    "specialty",
    "state",
    "city",
    "mart_created_at",

    # Ground truth — never in ML features
    "ground_truth_violation_count",
    "ground_truth_max_severity",
    "has_violation",

    # Categorical — not numeric
    "engagement_quadrant",
    "engagement_quadrant_reason",
    "cap_pattern",
    "spend_trend",

    # Activity proxies — not compliance signals
    "data_completeness_score",   # data artifact
    "has_speaker_events",        # activity flag
    "has_cms_payments",          # data artifact
    "has_interactions",          # data artifact

    # Circular heuristic scores
    # These are built FROM the same raw features
    # the IF already sees — including them means
    # the IF partially learns from our own scoring
    # logic rather than raw data independently
    "combined_raw_risk_score",
    "raw_spend_risk_score",
    "risk_signal_count",
    "raw_event_risk_score_mean",
]

# Ground truth columns extracted to separate parquet
GROUND_TRUTH_COLS = [
    "hcp_id",
    "ground_truth_violation_count",
    "ground_truth_max_severity",
]


# ─── Load functions ───────────────────────────────────────────────────────────

def load_spend_matrix() -> pd.DataFrame:
    """
    Load hcp_spend_feature_matrix.parquet produced by hcp_spend_features.py.

    Contains 97,011 rows (Athena HCP spine) with all spend features scaled.
    Raises FileNotFoundError if file is absent (run hcp_spend_features.py first).
    """
    path = Path(SPEND_MATRIX_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"Spend matrix not found at {path}. "
            "Run features/hcp_spend_features.py first (Athena required)."
        )
    df = pd.read_parquet(path)
    df["hcp_id"] = df["hcp_id"].astype(str)
    logger.info("Spend matrix: {} rows × {} columns", len(df), len(df.columns))
    return df


def load_event_matrix() -> pd.DataFrame:
    """
    Load event_feature_matrix.parquet produced by event_features.py.

    Contains ~1,354 rows (HCP speakers only) with aggregated event features.
    Raises FileNotFoundError if file is absent (run event_features.py first).
    """
    path = Path(EVENT_MATRIX_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"Event matrix not found at {path}. "
            "Run features/event_features.py first."
        )
    df = pd.read_parquet(path)
    df["hcp_id"] = df["hcp_id"].astype(str)
    logger.info("Event matrix: {} rows × {} columns (speaker HCPs only)", len(df), len(df.columns))
    return df


def load_risk_profile() -> pd.DataFrame:
    """
    Load interaction features and ground truth from mart_hcp_risk_profile (DuckDB).

    Selects columns not already present in the spend matrix:
      - Interaction features: total_interactions, total_meals, avg_meal_cost,
        interactions_with_vague_rationale, fmv_compliance_rate,
        unique_reps_interacted, interaction_frequency_score
      - Completeness signals: has_speaker_events, has_interactions,
        combined_raw_risk_score, risk_signal_count, data_completeness_score
      - Identity (excluded from features): city
      - Ground truth (kept separate): ground_truth_violation_count,
        ground_truth_max_severity

    NOTE: specialty, state, hcp_name, is_kol, is_high_prescriber,
    has_cms_payments are already in the spend matrix — not re-loaded here.
    """
    logger.info("Loading mart_hcp_risk_profile from DuckDB")
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
    except Exception as e:
        logger.error("DuckDB connection failed: {}", e)
        raise

    query = """
        SELECT
            hcp_id,
            specialty,
            state,
            hcp_name,
            is_high_prescriber,
            is_kol,
            city,
            combined_raw_risk_score,
            risk_signal_count,
            data_completeness_score,
            has_speaker_events,
            has_interactions,
            total_interactions,
            total_meals,
            avg_meal_cost,
            interactions_with_vague_rationale,
            fmv_compliance_rate,
            unique_reps_interacted,
            interaction_frequency_score,
            ground_truth_violation_count,
            ground_truth_max_severity
        FROM mart_hcp_risk_profile
    """
    df = con.execute(query).df()
    con.close()

    df["hcp_id"] = df["hcp_id"].astype(str)
    logger.info("Risk profile: {} rows × {} columns", len(df), len(df.columns))
    return df


def load_benchmark_context() -> pd.DataFrame:
    """
    Load engagement and outlier context columns from mart_benchmark (DuckDB).

    Selects engagement decision signals and combined risk flags — but NOT
    the spend-derived columns already in the spend matrix.

    Includes spend_2022/2023/2024 as _raw aliases for use in
    compute_real_benchmarks(). On DuckDB dev these are 0-filled;
    on Athena they contain real CMS data matching the spend matrix.

    NOTE: np_vs_industry_ratio_* and sow_* remain 0.0 here —
    industry/competitor data is Athena-only and not loaded in the Python layer.
    Planned for Phase 3.
    """
    logger.info("Loading mart_benchmark context from DuckDB")
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
    except Exception as e:
        logger.error("DuckDB connection failed: {}", e)
        raise

    query = """
        SELECT
            hcp_id,
            -- Raw spend for benchmark recompute (not scaled — see _real columns below)
            spend_2022 AS spend_2022_raw,
            spend_2023 AS spend_2023_raw,
            spend_2024 AS spend_2024_raw,
            -- dbt engagement decision (0-filled/categorical — superseded by _real below)
            engagement_quadrant,
            engagement_priority_score,
            cap_pattern,
            spend_trend,
            -- Cap exposure
            years_near_cap,
            years_at_cap,
            cap_breach_any,
            -- Combined risk flags
            sow_dominant_years_count,
            dual_outlier_flag,
            triple_signal_flag,
            escalating_risk_flag,
            chronic_risk_flag
        FROM mart_benchmark
    """
    df = con.execute(query).df()
    con.close()

    df["hcp_id"] = df["hcp_id"].astype(str)
    logger.info("Benchmark context: {} rows × {} columns", len(df), len(df.columns))
    return df


# ─── Merge ────────────────────────────────────────────────────────────────────

def merge_all(
    spend_df: pd.DataFrame,
    event_df: pd.DataFrame,
    risk_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """
    Left-join all sources on hcp_id using the spend matrix as the 97,011-row spine.

    Join order:
      spend_matrix (97,011 rows — Athena HCP spine)
        LEFT JOIN event_matrix    (~1,354 rows — 0-fill for non-speaker HCPs)
        LEFT JOIN risk_profile    (97,011 rows — interaction features + GT)
        LEFT JOIN benchmark       (97,011 rows — engagement/outlier signals)

    Returns:
      merged DataFrame (97,011 rows)
      merge_stats dict
    """
    spend_df = spend_df.set_index("hcp_id")
    event_df = event_df.set_index("hcp_id")
    risk_df  = risk_df.set_index("hcp_id")
    bm_df    = benchmark_df.set_index("hcp_id")

    # LEFT JOIN event features — non-speakers get NaN → 0-filled later
    merged = spend_df.join(event_df, how="left", rsuffix="_evt")

    # LEFT JOIN risk profile — interaction signals + GT
    merged = merged.join(risk_df, how="left", rsuffix="_rp")

    # LEFT JOIN benchmark context — engagement signals + _raw spend
    merged = merged.join(bm_df, how="left", rsuffix="_bm")

    # Drop any duplicate columns introduced by rsuffix (prefer left/original)
    dup_cols = [c for c in merged.columns if c.endswith(("_evt", "_rp", "_bm"))]
    if dup_cols:
        logger.debug("Dropping {} duplicate columns from joins: {}", len(dup_cols), dup_cols)
        merged = merged.drop(columns=dup_cols)

    merged = merged.reset_index()  # hcp_id back as column

    hcps_with_events = spend_df.index.isin(event_df.index).sum()
    hcps_with_both   = spend_df.index.isin(event_df.index).sum()  # both = events (all have spend)
    hcps_with_neither = len(spend_df) - hcps_with_events

    merge_stats = {
        "hcps_with_spend_data":  len(spend_df),
        "hcps_with_event_data":  len(event_df),
        "hcps_with_both":        int(hcps_with_both),
        "hcps_with_neither":     int(hcps_with_neither),
    }
    logger.info(
        "Merge complete: {} rows | {} with events | {} non-speakers (0-filled)",
        len(merged),
        hcps_with_events,
        hcps_with_neither,
    )
    return merged, merge_stats


# ─── Benchmark recompute ──────────────────────────────────────────────────────

# Resolves the Athena/DuckDB split documented in Tasks 2.3 and 2.4.
# Now that Athena spend data is available in Python memory we recompute
# benchmark signals that were 0-filled in dbt (mart_benchmark runs on DuckDB
# and cannot access mart_hcp_spend_features which is Athena-only).

def compute_real_benchmarks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recompute benchmark signals using real Athena spend data (now in Python memory).

    Inputs from merged_df:
        spend_2022_raw, spend_2023_raw, spend_2024_raw — from mart_benchmark
        specialty — from spend_matrix (COALESCE → 'Unknown' on DuckDB dev,
                    real specialty on Athena when HCP master is joined)

    7-step recompute:

    Step 1: Specialty peer averages per year
        peer_avg_2022/2023/2024 = mean(spend_YYYY) within specialty group

    Step 2: Nova Pharma percentile ranks
        np_spend_pct_rank_specialty_YYYY_real = percentile rank within specialty
        Uses pandas rank(pct=True, method='min') — equivalent to PERCENT_RANK()

    Step 3: Spend vs peer avg ratios (capped at 10.0)
        np_spend_vs_peer_avg_YYYY_real = spend_YYYY_raw / peer_avg_YYYY
        0.0 when peer_avg = 0 (all spend is 0 on DuckDB dev)

    Step 4: Outlier flags
        np_spend_outlier_YYYY_real = rank > 0.90
        np_outlier_years_count_real = sum of 3 yearly outlier flags
        np_persistent_outlier_real = outlier_years_count >= 2

    Step 5: Spend trend (ordinal integer)
        spend_trend_real: 3=increasing, 2=net_increasing, 1=stable, 0=decreasing

    Step 6: Cap pattern (ordinal integer)
        years_at_cap_real, years_near_cap_real from spend_YYYY_raw thresholds
        cap_pattern_real: 0=compliant … 4=chronic_breach

    Step 7: Engagement priority score
        engagement_priority_score_real:
            np_rank_2024_real × 30 (NP 2024 rank component)
          + np_outlier_years_count_real × 5 (persistence component, max 15)
        Note: industry/competitor components remain 0 (data not in Python layer)

    All _real columns are added with _real suffix to clearly distinguish from
    0-filled dbt versions and avoid overwriting them.

    Returns DataFrame with _real columns added.
    """
    specialty = df["specialty"].fillna("Unknown")
    s22 = df["spend_2022_raw"].fillna(0.0)
    s23 = df["spend_2023_raw"].fillna(0.0)
    s24 = df["spend_2024_raw"].fillna(0.0)

    # ── Step 1: Specialty peer averages ──────────────────────────────────────
    peer_avg = pd.DataFrame({
        "specialty": specialty,
        "s22": s22,
        "s23": s23,
        "s24": s24,
    }).groupby("specialty")[["s22", "s23", "s24"]].mean()

    peer_avg_2022 = specialty.map(peer_avg["s22"]).fillna(0.0)
    peer_avg_2023 = specialty.map(peer_avg["s23"]).fillna(0.0)
    peer_avg_2024 = specialty.map(peer_avg["s24"]).fillna(0.0)

    # ── Step 2: Percentile ranks ──────────────────────────────────────────────
    tmp = pd.DataFrame({"specialty": specialty, "s22": s22, "s23": s23, "s24": s24})
    for yr, col in [("2022", "s22"), ("2023", "s23"), ("2024", "s24")]:
        df[f"np_spend_pct_rank_specialty_{yr}_real"] = (
            tmp.groupby("specialty")[col]
               .rank(pct=True, method="min")
               .fillna(0.0)
               .values
        )

    # ── Step 3: Spend vs peer avg ratios (capped at 10.0) ────────────────────
    for yr, spend_col, avg_col in [
        ("2022", s22, peer_avg_2022),
        ("2023", s23, peer_avg_2023),
        ("2024", s24, peer_avg_2024),
    ]:
        df[f"np_spend_vs_peer_avg_{yr}_real"] = np.where(
            avg_col > 0.0,
            np.minimum(10.0, spend_col / avg_col),
            0.0,
        )

    # ── Step 4: Outlier flags and counts ─────────────────────────────────────
    df["np_spend_outlier_2022_real"] = (df["np_spend_pct_rank_specialty_2022_real"] > 0.90).astype(int)
    df["np_spend_outlier_2023_real"] = (df["np_spend_pct_rank_specialty_2023_real"] > 0.90).astype(int)
    df["np_spend_outlier_2024_real"] = (df["np_spend_pct_rank_specialty_2024_real"] > 0.90).astype(int)

    df["np_outlier_years_count_real"] = (
        df["np_spend_outlier_2022_real"]
        + df["np_spend_outlier_2023_real"]
        + df["np_spend_outlier_2024_real"]
    )
    df["np_persistent_outlier_real"] = (df["np_outlier_years_count_real"] >= 2).astype(int)

    # ── Step 5: Spend trend (ordinal integer) ─────────────────────────────────
    conditions = [
        (s24 > s23) & (s23 > s22),               # strictly increasing
        (s24 < s23) & (s23 < s22),               # strictly decreasing
        s24 > s22,                                # net increasing
    ]
    choices = [
        SPEND_TREND_ORDINAL["increasing"],
        SPEND_TREND_ORDINAL["decreasing"],
        SPEND_TREND_ORDINAL["net_increasing"],
    ]
    df["spend_trend_real"] = np.select(
        conditions, choices, default=SPEND_TREND_ORDINAL["stable"]
    )

    # ── Step 6: Cap pattern (ordinal integer) ─────────────────────────────────
    yrs_at_cap   = ((s22 >= ANNUAL_CAP).astype(int)
                   + (s23 >= ANNUAL_CAP).astype(int)
                   + (s24 >= ANNUAL_CAP).astype(int))
    yrs_near_cap = ((s22 >= NEAR_CAP_THRESHOLD).astype(int)
                   + (s23 >= NEAR_CAP_THRESHOLD).astype(int)
                   + (s24 >= NEAR_CAP_THRESHOLD).astype(int))

    df["years_at_cap_real"]   = yrs_at_cap
    df["years_near_cap_real"] = yrs_near_cap

    cap_conditions = [
        yrs_at_cap   >= 2,
        yrs_at_cap   == 1,
        yrs_near_cap >= 2,
        yrs_near_cap == 1,
    ]
    cap_choices = [
        CAP_PATTERN_ORDINAL["chronic_breach"],
        CAP_PATTERN_ORDINAL["single_breach"],
        CAP_PATTERN_ORDINAL["chronic_near_cap"],
        CAP_PATTERN_ORDINAL["near_cap"],
    ]
    df["cap_pattern_real"] = np.select(
        cap_conditions, cap_choices, default=CAP_PATTERN_ORDINAL["compliant"]
    )

    # ── Step 7: Engagement priority score ────────────────────────────────────
    # Prefer full 100pt score from industry_benchmarks.py if already computed.
    # Falls back to NP rank (30pts) + persistence (10pts max) ≈ 45pt cap.
    _POP_BM_PATH = Path(OUTPUT_DIR) / "population_benchmarks.parquet"

    np_rank_2024  = df["np_spend_pct_rank_specialty_2024_real"]
    outlier_count = df["np_outlier_years_count_real"].astype(float)

    base_score = np.minimum(
        40.0,
        np_rank_2024 * 30.0 + np.minimum(10.0, outlier_count * 5.0),
    )

    if _POP_BM_PATH.exists():
        try:
            pop_bm = pd.read_parquet(_POP_BM_PATH)[["hcp_id", "engagement_priority_score_full"]]
            pop_bm = pop_bm.set_index("hcp_id")["engagement_priority_score_full"]
            hcp_ids = df["hcp_id"] if "hcp_id" in df.columns else df.index
            eps_full = hcp_ids.map(pop_bm).fillna(base_score)
            df["engagement_priority_score_real"] = np.minimum(100.0, eps_full.values)
            logger.info(
                "EPS loaded from population_benchmarks.parquet — "
                "mean={:.1f}  max={:.1f}",
                df["engagement_priority_score_real"].mean(),
                df["engagement_priority_score_real"].max(),
            )
        except Exception as exc:
            logger.warning("Failed to load population_benchmarks.parquet: {} — using base score", exc)
            df["engagement_priority_score_real"] = np.minimum(100.0, base_score)
    else:
        df["engagement_priority_score_real"] = np.minimum(100.0, base_score)
        logger.info(
            "population_benchmarks.parquet not found — using base EPS "
            "(run features/industry_benchmarks.py to compute full 100pt score)"
        )

    # Logging
    nonzero_rank = (df["np_spend_pct_rank_specialty_2024_real"] > 0).sum()
    logger.info(
        "Benchmark recompute: {} HCPs with non-zero 2024 rank "
        "(0 on DuckDB dev — real values require Athena spend data)",
        nonzero_rank,
    )

    quadrant_after = pd.cut(
        df["np_spend_pct_rank_specialty_2024_real"],
        bins=[-0.001, 0.25, 0.50, 0.75, 0.90, 1.001],
        labels=["0-25%", "25-50%", "50-75%", "75-90%", "90-100%"],
    ).value_counts().sort_index()
    logger.info("2024 rank distribution (real):\n{}", quadrant_after.to_string())

    logger.info(
        "Cap pattern distribution (real): {}",
        dict(df["cap_pattern_real"].value_counts()),
    )
    return df


# ─── Ground truth extraction ─────────────────────────────────────────────────

def extract_ground_truth(df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract ground truth columns into a separate DataFrame.

    Output: hcp_id, ground_truth_violation_count, ground_truth_max_severity,
            has_violation (ground_truth_violation_count > 0)

    VALIDATION ONLY — never merged back into the feature matrix.

    The ground truth represents violations detected by the synthetic data
    generator based on the same compliance rules applied in the dbt marts.
    It is used exclusively in test_anomaly_models.py (Task 2.12) to measure
    how well the Isolation Forest surfaces true violations.
    """
    gt = df[GROUND_TRUTH_COLS].drop_duplicates(subset=["hcp_id"]).copy()
    gt["has_violation"] = (gt["ground_truth_violation_count"] > 0).astype(int)

    total_violations = gt["has_violation"].sum()
    violation_rate   = gt["has_violation"].mean()
    severity_dist    = gt["ground_truth_max_severity"].value_counts().to_dict()

    logger.info(
        "Ground truth: {} HCPs with violations ({:.1f}%) | severity: {}",
        total_violations,
        violation_rate * 100,
        severity_dist,
    )
    return gt


# ─── Feature matrix ───────────────────────────────────────────────────────────

def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build clean numeric feature matrix from merged DataFrame.

    Steps:
      1. Drop EXCLUDE_FROM_FEATURES columns (identity, GT, categoricals, _raw)
      2. Drop any remaining object/string columns (safe catch-all)
      3. Fill remaining nulls with 0.0 (event columns for non-speakers, etc.)
      4. Ensure all columns are float64 or int64
      5. Drop any infinite values (replace with 0.0)

    Returns feature matrix with no nulls, no strings, no GT columns.
    """
    feat_df = df.drop(
        columns=[c for c in EXCLUDE_FROM_FEATURES if c in df.columns],
        errors="ignore",
    )

    # Drop any remaining string/object columns
    str_cols = feat_df.select_dtypes(include=["object", "category"]).columns.tolist()
    if str_cols:
        logger.debug("Dropping {} remaining string columns: {}", len(str_cols), str_cols)
        feat_df = feat_df.drop(columns=str_cols)

    # Drop boolean columns — encode as int
    bool_cols = feat_df.select_dtypes(include="bool").columns.tolist()
    for col in bool_cols:
        feat_df[col] = feat_df[col].fillna(False).astype(int)

    # Fill nulls (event 0-fill for non-speakers, any residual from joins)
    null_count = feat_df.isnull().sum().sum()
    if null_count > 0:
        logger.info("Filling {} residual nulls with 0.0", null_count)
        feat_df = feat_df.fillna(0.0)

    # Replace infinities
    inf_count = np.isinf(feat_df.select_dtypes(include="number")).sum().sum()
    if inf_count > 0:
        logger.warning("Replacing {} infinite values with 0.0", inf_count)
        feat_df = feat_df.replace([np.inf, -np.inf], 0.0)

    logger.info(
        "Feature matrix: {} rows × {} feature columns",
        len(feat_df),
        len(feat_df.columns),
    )
    return feat_df


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_feature_store(
    feature_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
) -> bool:
    """
    Validate feature store and ground truth outputs.

    Checks:
      - feature_df rows == 97,011
      - ground_truth_df rows == 97,011
      - No nulls in feature_df
      - No infinite values in feature_df
      - No string columns in feature_df
      - ground_truth_df has exactly 4 columns (hcp_id + 3 GT cols)
      - has_violation rate in [0.20, 0.30] (expected ~24.5%)
      - No overlap between feature column names and GT column names

    Raises ValueError on critical failures.
    Returns True if all pass.
    """
    EXPECTED_ROWS = 97_011
    EXPECTED_VIOLATION_RATE_MIN = 0.20
    EXPECTED_VIOLATION_RATE_MAX = 0.30

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

    _check("feature_df row count", len(feature_df) == EXPECTED_ROWS,
           f"{len(feature_df)} (expected {EXPECTED_ROWS})")

    _check("ground_truth_df row count", len(ground_truth_df) == EXPECTED_ROWS,
           f"{len(ground_truth_df)} (expected {EXPECTED_ROWS})")

    null_total = feature_df.isnull().sum().sum()
    _check("No nulls in feature_df", null_total == 0, f"{null_total} nulls")

    inf_count = np.isinf(feature_df.select_dtypes(include="number")).sum().sum()
    _check("No infinite values", inf_count == 0, f"{inf_count} infinities")

    str_cols = feature_df.select_dtypes(include=["object", "category"]).columns.tolist()
    _check("No string columns in feature_df", len(str_cols) == 0,
           f"String cols: {str_cols}")

    _check("ground_truth_df has 4 columns",
           len(ground_truth_df.columns) == 4,
           f"Found: {list(ground_truth_df.columns)}")

    violation_rate = ground_truth_df["has_violation"].mean()
    _check(
        "has_violation rate in expected range",
        EXPECTED_VIOLATION_RATE_MIN <= violation_rate <= EXPECTED_VIOLATION_RATE_MAX,
        f"{violation_rate:.3f} (expected {EXPECTED_VIOLATION_RATE_MIN}–{EXPECTED_VIOLATION_RATE_MAX})",
    )

    gt_feature_cols = {"ground_truth_violation_count", "ground_truth_max_severity", "has_violation"}
    overlap = gt_feature_cols & set(feature_df.columns)
    _check("No GT columns in feature_df", len(overlap) == 0, f"Overlap: {overlap}")

    logger.info("Validation: {}/{} checks passed",
                checks_passed, checks_passed + checks_failed)

    if checks_failed > 0:
        raise ValueError(
            f"Feature store validation failed: {checks_failed} check(s) — see logs above"
        )
    return True


# ─── Output ───────────────────────────────────────────────────────────────────
import numpy as np

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

def save_outputs(
    feature_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
    merge_stats: dict,
    benchmark_recompute_stats: dict,
    ground_truth_stats: dict,
) -> dict:
    """
    Save feature store, ground truth, and metadata to features/outputs/.

    feature_store.parquet:
        Final ML-ready feature matrix (97,011 rows, no GT, no nulls).
        Read by Isolation Forest (Task 2.9) and rule_based_flags.py (Task 2.8).

    ground_truth_labels.parquet:
        Ground truth validation set (97,011 rows, 4 columns).
        Read ONLY by test_anomaly_models.py (Task 2.12).
        Never merged into feature_store.parquet.

    Returns dict of output paths.
    """
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    fs_path   = out_dir / "feature_store.parquet"
    gt_path   = out_dir / "ground_truth_labels.parquet"
    meta_path = out_dir / "feature_store_metadata.json"

    feature_df.to_parquet(fs_path, index=False)
    logger.info("Saved feature store: {} ({} rows)", fs_path, len(feature_df))

    ground_truth_df.to_parquet(gt_path, index=False)
    logger.info("Saved ground truth: {} ({} rows)", gt_path, len(ground_truth_df))

    metadata = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "row_count":       len(feature_df),
        "feature_count":   len(feature_df.columns),
        "sources": {
            "spend_matrix": SPEND_MATRIX_PATH,
            "event_matrix": EVENT_MATRIX_PATH,
            "risk_profile": f"duckdb: mart_hcp_risk_profile",
            "benchmark":    f"duckdb: mart_benchmark",
        },
        "merge_stats":               merge_stats,
        "benchmark_recompute":       benchmark_recompute_stats,
        "ground_truth":              ground_truth_stats,
        "feature_columns":           feature_df.columns.tolist(),
        "excluded_columns":          EXCLUDE_FROM_FEATURES,
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, cls=NumpyEncoder)
    logger.info("Saved metadata: {}", meta_path)

    return {
        "feature_store":    str(fs_path),
        "ground_truth":     str(gt_path),
        "metadata":         str(meta_path),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info("feature_store.py — Phase 2 central feature store")
    logger.info("=" * 60)

    # 1. Load all sources
    spend_df     = load_spend_matrix()
    event_df     = load_event_matrix()
    risk_df      = load_risk_profile()
    benchmark_df = load_benchmark_context()

    # 2. Merge on hcp_id (LEFT JOIN from spend spine)
    merged_df, merge_stats = merge_all(spend_df, event_df, risk_df, benchmark_df)

    # 3. Recompute benchmark signals using real Athena spend data
    merged_df = compute_real_benchmarks(merged_df)

    # Collect benchmark recompute stats for metadata
    benchmark_recompute_stats = {
        "hcps_with_real_benchmarks": int(
            (merged_df["np_spend_pct_rank_specialty_2024_real"] > 0).sum()
        ),
        "cap_pattern_real_distribution": dict(
            merged_df["cap_pattern_real"].value_counts().astype(int)
        ),
        "spend_trend_real_distribution": dict(
            merged_df["spend_trend_real"].value_counts().astype(int)
        ),
    }

    # 4. Extract ground truth BEFORE building feature matrix
    ground_truth_df = extract_ground_truth(merged_df)

    # Save unscaled version for rule_based_flags.py
    # Rule checks must run on original values not RobustScaled values
    raw_path = os.path.join(OUTPUT_DIR, "feature_store_raw.parquet")
    merged_df.to_parquet(raw_path, index=False)
    logger.info(f"Saved raw (unscaled) feature store: {raw_path}")

    feature_df = build_feature_matrix(merged_df)

    # 6. Validate
    validate_feature_store(feature_df, ground_truth_df)
    # ADD before line 794 (before save_outputs call)
    ground_truth_stats = {
        "total_violations": int(ground_truth_df["has_violation"].sum()),
        "violation_rate": float(ground_truth_df["has_violation"].mean()),
        "severity_distribution": ground_truth_df["ground_truth_max_severity"].value_counts().to_dict()
    }
    # 7. Save
    output_paths = save_outputs(
        feature_df,
        ground_truth_df,
        merge_stats,
        benchmark_recompute_stats,
        ground_truth_stats,
    )

    elapsed = time.time() - start

    logger.info("")
    logger.info("─" * 60)
    logger.info("Complete.")
    logger.info("  Total HCPs:                  {}", len(feature_df))
    logger.info("  Feature columns:             {}", len(feature_df.columns))
    logger.info("  HCPs with violations:        {} ({:.1f}%)",
                ground_truth_stats["total_violations"],
                ground_truth_stats["violation_rate"] * 100)
    logger.info("  HCPs with real bench scores: {}",
                benchmark_recompute_stats["hcps_with_real_benchmarks"])
    logger.info("  Cap pattern distribution:    {}",
                benchmark_recompute_stats["cap_pattern_real_distribution"])
    logger.info("  Output: {}/", OUTPUT_DIR)
    logger.info("  Time taken: {:.1f}s", elapsed)
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
