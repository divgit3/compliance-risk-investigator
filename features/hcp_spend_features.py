"""
hcp_spend_features.py
Phase 2 — HCP spend feature engineering pipeline

Reads mart_hcp_spend_features from Athena and mart_benchmark from DuckDB.
Applies null handling, binary encoding, and RobustScaler.
Outputs a clean ML-ready feature matrix (one row per HCP) and metadata JSON.

This script bridges the dbt mart layer and the ML model layer, and begins
resolving the Athena/DuckDB split by reading from both sources and aligning
on hcp_id.

Usage:
    python3 features/hcp_spend_features.py
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
from pyathena import connect
from sklearn.preprocessing import RobustScaler

# ─── Environment ─────────────────────────────────────────────────────────────
load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
ATHENA_DATABASE = "awsdatacatalog"
ATHENA_SCHEMA   = "compliance_risk_raw"
ATHENA_TABLE    = "mart_hcp_spend_features"
S3_STAGING_DIR  = os.getenv(
    "ATHENA_S3_STAGING_DIR",
    "s3://compliance-risk-investigator/athena-query-output/",
)

DUCKDB_PATH = "data/processed/compliance.duckdb"

OUTPUT_DIR = "features/outputs"

# Annual compensation cap (COMP_001 — compliance/rules.json)
ANNUAL_CAP = 75_000.0

# ─── Feature lists ───────────────────────────────────────────────────────────

# Continuous numeric features from mart_hcp_spend_features (Athena).
# annual_cap_pct_used_2022/2023/2024 are derived in load_athena_spend_features()
# from spend_2022/2023/2024 / ANNUAL_CAP (mart_hcp_spend_features has a single
# annual_cap_pct_used column based on peak year, not per-year).
SPEND_FEATURES = [
    # Annual spend per year
    "spend_2022",
    "spend_2023",
    "spend_2024",
    "peak_year_spend",
    # Annual cap signals (derived: spend_YYYY / 75000.0)
    "annual_cap_pct_used_2022",
    "annual_cap_pct_used_2023",
    "annual_cap_pct_used_2024",
    # Meal breach signals
    "meals_over_limit_count",
    "meal_breach_rate",
    "max_meal_overage_pct",
    # Payment mix
    "pct_food_beverage",
    "pct_speaking_fee",
    "pct_consulting",
    "speaking_fee_total",
    "speaking_fee_count",
    # Rep concentration
    "avg_unique_reps",
    "top_rep_concentration_pct",
    # Trend signals
    "yoy_growth_2223",
    "yoy_growth_2324",
    # Pre-ML heuristic score (scaled separately to 0–1)
    "raw_spend_risk_score",
]

# Boolean columns — converted to 0/1 integer, not scaled.
SPEND_BINARY_FEATURES = [
    "at_cap_flag",
    "near_cap_flag",
    "multi_year_increasing_flag",
    "has_cms_payments",
    # is_high_prescriber and is_kol come from DuckDB mart_hcp_risk_profile
    # joined in feature_store.py — not available in Athena mart
]

# Benchmark features from mart_benchmark (DuckDB).
# Percentile ranks (0.0–1.0) are not rescaled.
# np_persistent_outlier and np_escalating_rank are boolean — encoded as 0/1.
BENCHMARK_FEATURES = [
    "np_spend_pct_rank_specialty_2024",
    "np_spend_pct_rank_specialty_2023",
    "np_spend_pct_rank_specialty_2022",
    "np_spend_vs_peer_avg_2024",
    "np_outlier_years_count",
    "np_persistent_outlier",
    "np_escalating_rank",
    "engagement_priority_score",
]

# Percentile rank columns — already 0.0–1.0, not rescaled.
PERCENTILE_RANK_FEATURES = [
    "np_spend_pct_rank_specialty_2024",
    "np_spend_pct_rank_specialty_2023",
    "np_spend_pct_rank_specialty_2022",
]

# Boolean benchmark features — encoded as 0/1, not scaled.
BENCHMARK_BINARY_FEATURES = [
    "np_persistent_outlier",
    "np_escalating_rank",
]

# Identity columns — preserved for output joining, not ML features.
IDENTITY_COLUMNS = ["hcp_id"]


# ─── Data loading ─────────────────────────────────────────────────────────────

def load_athena_spend_features() -> pd.DataFrame:
    """
    Connect to Athena and read mart_hcp_spend_features.

    Selects hcp_id, identity columns, all SPEND_FEATURES, and SPEND_BINARY_FEATURES.
    Derives annual_cap_pct_used_2022/2023/2024 from per-year spend columns since
    mart_hcp_spend_features only stores a single peak-year annual_cap_pct_used.

    Returns DataFrame with hcp_id as index.
    Raises on connection failure.
    """
    logger.info("Connecting to Athena: {}.{}.{}", ATHENA_DATABASE, ATHENA_SCHEMA, ATHENA_TABLE)

    try:
        conn = connect(
            s3_staging_dir=S3_STAGING_DIR,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
    except Exception as e:
        logger.error("Athena connection failed: {}", e)
        raise

    # Columns that exist directly in mart_hcp_spend_features.
    # annual_cap_pct_used_2022/2023/2024 are derived below — not queried.
    direct_spend_cols = [
        c for c in SPEND_FEATURES
        if c not in ("annual_cap_pct_used_2022", "annual_cap_pct_used_2023", "annual_cap_pct_used_2024")
    ]
    identity_in_mart = []
    select_cols = (
        ["hcp_id"]
        + identity_in_mart
        + direct_spend_cols
        + SPEND_BINARY_FEATURES
    )
    col_list = ", ".join(select_cols)
    query = f"SELECT {col_list} FROM {ATHENA_SCHEMA}.{ATHENA_TABLE}"

    logger.info("Running Athena query (may take 30–60s on cold start)...")
    try:
        df = pd.read_sql(query, conn)
    except Exception as e:
        logger.error("Athena query failed: {}", e)
        raise

    logger.info("Athena load: {} rows × {} columns", len(df), len(df.columns))

    # Derive per-year cap columns from per-year spend.
    # mart_hcp_spend_features has annual_cap_pct_used (peak-year only).
    # We derive per-year versions here for the feature matrix.
    df["annual_cap_pct_used_2022"] = df["spend_2022"] / ANNUAL_CAP
    df["annual_cap_pct_used_2023"] = df["spend_2023"] / ANNUAL_CAP
    df["annual_cap_pct_used_2024"] = df["spend_2024"] / ANNUAL_CAP
    logger.debug("Derived annual_cap_pct_used_2022/2023/2024 from per-year spend")

    df["hcp_id"] = df["hcp_id"].astype(str)
    df = df.set_index("hcp_id")
    return df


def load_duckdb_benchmark_features() -> pd.DataFrame:
    """
    Connect to DuckDB and read mart_benchmark benchmark features.

    Returns DataFrame with hcp_id as index.
    On DuckDB, spend-derived ranks (np_spend_pct_rank_*) are 0.0 for all HCPs
    since CMS spend data is Athena-only. Risk score ranks are meaningful.

    Logs a warning and returns an empty DataFrame on connection failure
    so spend features alone can proceed to the ML model.
    """
    logger.info("Connecting to DuckDB: {}", DUCKDB_PATH)

    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
    except Exception as e:
        logger.warning(
            "DuckDB connection failed — continuing without benchmark features: {}", e
        )
        return pd.DataFrame()

    col_list = ", ".join(["hcp_id"] + BENCHMARK_FEATURES)
    query = f"SELECT {col_list} FROM mart_benchmark"

    try:
        df = con.execute(query).df()
    except Exception as e:
        logger.warning(
            "DuckDB query failed — continuing without benchmark features: {}", e
        )
        con.close()
        return pd.DataFrame()

    con.close()
    logger.info("DuckDB load: {} rows × {} benchmark columns", len(df), len(df.columns) - 1)

    df["hcp_id"] = df["hcp_id"].astype(str)
    df = df.set_index("hcp_id")
    return df


# ─── Feature merging ─────────────────────────────────────────────────────────

def merge_features(spend_df: pd.DataFrame, benchmark_df: pd.DataFrame) -> pd.DataFrame:
    """
    Left-join spend_df with benchmark_df on hcp_id.

    All 97,011 HCPs from Athena are preserved.
    HCPs with no benchmark data receive 0.0 fill downstream in handle_nulls().
    """
    if benchmark_df.empty:
        logger.warning("benchmark_df is empty — benchmark columns will be 0-filled")
        merged = spend_df.copy()
        for col in BENCHMARK_FEATURES:
            merged[col] = 0.0
        return merged

    merged = spend_df.join(benchmark_df, how="left", rsuffix="_bench")

    matched = benchmark_df.index.isin(spend_df.index).sum()
    unmatched = len(spend_df) - spend_df.index.isin(benchmark_df.index).sum()

    logger.info(
        "Merge: {} total HCPs | {} with benchmark data | {} without",
        len(merged),
        matched,
        unmatched,
    )
    return merged


# ─── Null handling ────────────────────────────────────────────────────────────

def handle_nulls(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply null fill strategy per feature type.

    yoy_growth (yoy_growth_2223, yoy_growth_2324):
        Fill with 0.0 — null means the HCP had no prior-year spend, which is
        not a risk signal. Treat as no growth rather than unknown.

    Ratio features (top_rep_concentration_pct, pct_* etc.):
        Fill with 0.0 — null means no activity was recorded.

    Binary features (SPEND_BINARY_FEATURES + BENCHMARK_BINARY_FEATURES):
        Fill with 0 (integer) — null means the flag is absent/false.

    Percentile ranks (PERCENTILE_RANK_FEATURES):
        Fill with 0.0 — null means no peer data available; treat as floor rank.

    All remaining numeric nulls:
        Fill with 0.0 as a safe default.
    """
    null_counts_before = df.isnull().sum()
    filled_cols = null_counts_before[null_counts_before > 0]
    if not filled_cols.empty:
        logger.info("Nulls before fill:\n{}", filled_cols.to_string())

    # YoY growth: 0.0 = no prior year data, not a risk signal
    for col in ("yoy_growth_2223", "yoy_growth_2324"):
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    # Percentile ranks: 0.0 = floor, no peer data
    for col in PERCENTILE_RANK_FEATURES:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    # Binary spend features: 0 = false/absent
    for col in SPEND_BINARY_FEATURES:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # Binary benchmark features: 0 = false/absent
    for col in BENCHMARK_BINARY_FEATURES:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # All remaining nulls: 0.0
    df = df.fillna(0.0)

    null_counts_after = df.isnull().sum().sum()
    logger.info(
        "Null fill complete: {} nulls filled → {} remaining",
        filled_cols.sum() if not filled_cols.empty else 0,
        null_counts_after,
    )
    return df


# ─── Encoding ─────────────────────────────────────────────────────────────────

def encode_binary_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert all boolean columns in SPEND_BINARY_FEATURES and
    BENCHMARK_BINARY_FEATURES to integer (0/1).

    Boolean dtype columns (True/False) are cast to int64.
    Object dtype columns with True/False strings are mapped.
    """
    all_binary = SPEND_BINARY_FEATURES + BENCHMARK_BINARY_FEATURES
    encoded = []
    for col in all_binary:
        if col not in df.columns:
            continue
        if df[col].dtype == bool or str(df[col].dtype) == "boolean":
            df[col] = df[col].astype("Int64").fillna(0).astype(int)
            encoded.append(col)
        elif df[col].dtype == object:
            df[col] = df[col].map({"True": 1, "False": 0, True: 1, False: 0}).fillna(0).astype(int)
            encoded.append(col)
        else:
            # Already numeric — ensure int
            df[col] = df[col].fillna(0).astype(int)
            encoded.append(col)

    logger.info("Binary encoding: {} columns converted to int64", len(encoded))
    logger.debug("Encoded: {}", encoded)
    return df


# ─── Scaling ──────────────────────────────────────────────────────────────────

def scale_features(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Apply RobustScaler to continuous spend and benchmark features.

    RobustScaler uses median and IQR rather than mean and std, making it
    resistant to the extreme outliers present by design in compliance data
    (e.g. HCPs at 2x the annual cap).

    NOT scaled:
      - Binary features (already 0/1)
      - Percentile rank features (already 0.0–1.0)
      - raw_spend_risk_score (scaled separately to 0–1 below)
      - engagement_priority_score (already 0–100, scaled with RobustScaler)
      - Identity columns (not numeric)

    Returns:
      - scaled DataFrame
      - scaler_params dict: {column: {center: float, scale: float}}
    """
    all_binary = set(SPEND_BINARY_FEATURES + BENCHMARK_BINARY_FEATURES)
    do_not_scale = all_binary | set(PERCENTILE_RANK_FEATURES) | {"raw_spend_risk_score"}
    identity_like = set(IDENTITY_COLUMNS)

    # Columns eligible for RobustScaler
    scale_cols = [
        c for c in (SPEND_FEATURES + BENCHMARK_FEATURES)
        if c in df.columns
        and c not in do_not_scale
        and c not in identity_like
    ]

    scaler = RobustScaler()
    df[scale_cols] = scaler.fit_transform(df[scale_cols])

    scaler_params = {
        col: {
            "center": float(scaler.center_[i]),
            "scale":  float(scaler.scale_[i]),
        }
        for i, col in enumerate(scale_cols)
    }

    # raw_spend_risk_score: scale to 0–1 (divide by 100)
    if "raw_spend_risk_score" in df.columns:
        df["raw_spend_risk_score"] = df["raw_spend_risk_score"] / 100.0
        scaler_params["raw_spend_risk_score"] = {"center": 0.0, "scale": 100.0}

    logger.info(
        "Scaling: RobustScaler applied to {} columns + raw_spend_risk_score /100",
        len(scale_cols),
    )
    return df, scaler_params


# ─── Identity columns ─────────────────────────────────────────────────────────

def add_identity_columns(scaled_df: pd.DataFrame, original_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add back non-feature identity columns removed before scaling.

    hcp_id is needed for output joining
    but are not ML features. They are sourced from original_df (pre-scale).
    """
    for col in IDENTITY_COLUMNS:
        if col == "hcp_id":
            # hcp_id is the index — restore as column
            scaled_df["hcp_id"] = scaled_df.index
        elif col in original_df.columns:
            scaled_df[col] = original_df[col]
        else:
            scaled_df[col] = None

    logger.debug("Identity columns restored: {}", IDENTITY_COLUMNS)
    return scaled_df


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_output(df: pd.DataFrame) -> bool:
    """
    Run basic validation checks on the output feature matrix.

    Checks:
      - Row count == 97,011 (expected Athena row count)
      - No nulls in feature columns
      - All feature columns are float64 or int64
      - Binary columns contain only 0 or 1
      - No infinite values
      - raw_spend_risk_score in [0, 1] after /100 scaling

    Raises ValueError if any check fails.
    Returns True if all pass.
    """
    EXPECTED_ROWS = 97_011
    all_feature_cols = SPEND_FEATURES + BENCHMARK_FEATURES

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

    # Row count
    _check(
        "Row count",
        len(df) == EXPECTED_ROWS,
        f"{len(df)} rows (expected {EXPECTED_ROWS})",
    )

    # No nulls in feature columns
    feature_nulls = df[[c for c in all_feature_cols if c in df.columns]].isnull().sum().sum()
    _check("No nulls in feature columns", feature_nulls == 0, f"{feature_nulls} nulls found")

    # All feature columns numeric
    non_numeric = [
        c for c in all_feature_cols
        if c in df.columns and not pd.api.types.is_numeric_dtype(df[c])
    ]
    _check("All feature columns numeric", len(non_numeric) == 0, f"Non-numeric: {non_numeric}")

    # Binary columns are 0/1 only
    all_binary = SPEND_BINARY_FEATURES + BENCHMARK_BINARY_FEATURES
    bad_binary = []
    for col in all_binary:
        if col in df.columns:
            vals = set(df[col].unique())
            if not vals.issubset({0, 1}):
                bad_binary.append(f"{col}: {vals}")
    _check("Binary columns are 0 or 1 only", len(bad_binary) == 0, "; ".join(bad_binary))

    # No infinite values
    feature_df = df[[c for c in all_feature_cols if c in df.columns]]
    inf_count = np.isinf(feature_df.select_dtypes(include="number")).sum().sum()
    _check("No infinite values", inf_count == 0, f"{inf_count} inf values found")

    # raw_spend_risk_score in [0, 1] after scaling
    if "raw_spend_risk_score" in df.columns:
        rmin = df["raw_spend_risk_score"].min()
        rmax = df["raw_spend_risk_score"].max()
        _check(
            "raw_spend_risk_score in [0, 1]",
            rmin >= 0.0 and rmax <= 1.0,
            f"range [{rmin:.4f}, {rmax:.4f}]",
        )

    logger.info("Validation: {}/{} checks passed", checks_passed, checks_passed + checks_failed)

    if checks_failed > 0:
        raise ValueError(f"Validation failed: {checks_failed} check(s) failed — see logs above")

    return True


# ─── Output ───────────────────────────────────────────────────────────────────

def save_outputs(df: pd.DataFrame, scaler_params: dict) -> dict:
    """
    Save feature matrix as parquet and metadata as JSON.

    Returns dict of output paths.
    """
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    matrix_path = out_dir / "hcp_spend_feature_matrix.parquet"
    metadata_path = out_dir / "hcp_spend_feature_metadata.json"

    # Parquet
    df.to_parquet(matrix_path, index=False)
    logger.info("Saved feature matrix: {} ({} rows)", matrix_path, len(df))

    # Metadata JSON
    feature_cols = [c for c in (SPEND_FEATURES + BENCHMARK_FEATURES) if c in df.columns]
    binary_cols  = [c for c in (SPEND_BINARY_FEATURES + BENCHMARK_BINARY_FEATURES) if c in df.columns]
    identity_cols = [c for c in IDENTITY_COLUMNS if c in df.columns]

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_tables": [
            f"athena: {ATHENA_SCHEMA}.{ATHENA_TABLE}",
            f"duckdb: mart_benchmark",
        ],
        "row_count": len(df),
        "feature_columns": feature_cols,
        "binary_columns": binary_cols,
        "identity_columns": identity_cols,
        "scaler": "RobustScaler",
        "scaler_params": scaler_params,
        "null_fill_strategy": {
            "yoy_growth":       "0.0 — no prior year data, not a risk signal",
            "ratios":           "0.0 — no activity recorded",
            "binary":           "0 — false/absent",
            "percentile_ranks": "0.0 — no peer data available",
        },
    }

    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved metadata: {}", metadata_path)

    return {
        "feature_matrix": str(matrix_path),
        "metadata":       str(metadata_path),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info("hcp_spend_features.py — Phase 2 feature engineering")
    logger.info("Config: ATHENA_TABLE={} | DUCKDB_PATH={}", ATHENA_TABLE, DUCKDB_PATH)
    logger.info("=" * 60)

    # 1. Load from Athena
    spend_df = load_athena_spend_features()

    # 2. Load from DuckDB (non-fatal on failure)
    benchmark_df = load_duckdb_benchmark_features()

    # Keep a copy of the raw loaded data for identity column restoration
    original_df = spend_df.copy()

    # 3. Merge
    merged_df = merge_features(spend_df, benchmark_df)

    # 4. Null handling
    clean_df = handle_nulls(merged_df)

    # 5. Binary encoding
    encoded_df = encode_binary_features(clean_df)

    # Save raw dollar amounts before scaling
    raw_spend_cols = ["spend_2022", "spend_2023", "spend_2024",
                    "peak_year_spend", "annual_cap_pct_used",
                    "annual_cap_pct_used_2022", "annual_cap_pct_used_2023",
                    "annual_cap_pct_used_2024",
                    "meal_breach_rate", "max_meal_overage_pct",
                    "meals_over_limit_count",
                    "fmv_compliance_rate",
                    "interactions_with_vague_rationale",
                    "total_interactions"]
    raw_spend_cols_present = [c for c in raw_spend_cols if c in encoded_df.columns]
    raw_dollar_df = encoded_df[raw_spend_cols_present].copy()
    # hcp_id lives in merged_df as identity column — add it back
    raw_dollar_path = os.path.join(OUTPUT_DIR, "hcp_spend_raw_dollars.parquet")
    raw_dollar_df.insert(0, "hcp_id", merged_df.index.values)
    raw_dollar_df.to_parquet(raw_dollar_path, index=False)
    logger.info(f"Saved raw dollar amounts: {raw_dollar_path} ({len(raw_dollar_df)} rows)")

    scaled_df, scaler_params = scale_features(encoded_df)
    final_df = add_identity_columns(scaled_df, merged_df)
    validate_output(final_df)
    save_outputs(final_df, scaler_params)

    elapsed = time.time() - start
    feature_cols = [c for c in (SPEND_FEATURES + BENCHMARK_FEATURES) if c in final_df.columns]
    binary_cols  = [c for c in (SPEND_BINARY_FEATURES + BENCHMARK_BINARY_FEATURES) if c in final_df.columns]

    logger.info("")
    logger.info("─" * 60)
    logger.info("Complete.")
    logger.info("  Rows:            {}", len(final_df))
    logger.info("  Feature columns: {}", len(feature_cols))
    logger.info("  Binary columns:  {}", len(binary_cols))
    logger.info("  Output:          {}/", OUTPUT_DIR)
    logger.info("  Time taken:      {:.1f}s", elapsed)
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
