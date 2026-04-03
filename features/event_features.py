"""
event_features.py
Phase 2 — Speaker event feature engineering pipeline

Reads mart_event_features from DuckDB (5,241 event-level rows).
Aggregates to one row per HCP speaker (1,354 rows).
Applies null handling, RobustScaler on cost/risk aggregations.
Outputs a clean ML-ready event feature matrix and metadata JSON.

This matrix is merged with hcp_spend_feature_matrix.parquet
in Task 2.7 (feature_store.py) on hcp_id with 0-fill for the
95,657 HCPs who have no speaker events.

Usage:
    python3 features/event_features.py
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
from sklearn.preprocessing import RobustScaler

# ─── Environment ─────────────────────────────────────────────────────────────
load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
DUCKDB_PATH = "data/processed/compliance.duckdb"
OUTPUT_DIR  = "features/outputs"

SOURCE_TABLE     = "mart_event_features"
EXPECTED_EVENTS  = 5_241
EXPECTED_SPEAKERS = 1_354

# ─── Feature definitions ─────────────────────────────────────────────────────

# Continuous event-level columns → aggregations applied per HCP speaker.
#
# Aggregation semantics:
#   mean = typical event for this speaker (baseline behavior)
#   max  = worst event (single worst-case exposure)
#   sum  = total exposure across all events
#   min  = best-case floor (used for attendance and attestation)
#   std  = variability of signal across events
#
# Column name convention after flattening:
#   {column}_{agg}  e.g. raw_event_risk_score_mean, speaker_fee_max
EVENT_AGG_FEATURES: dict[str, list[str]] = {
    # Risk score aggregations
    "raw_event_risk_score": ["mean", "max", "std"],

    # Attendance signals (SPEAKER_004: min 3 attendees)
    "attendee_count": ["mean", "min", "sum"],

    # Cost signals (VENUE_001: $3K venue, VENUE_002: $8K total, MEAL_003: $100/head)
    "speaker_fee":         ["mean", "max", "sum"],
    "total_program_cost":  ["mean", "max", "sum"],
    "meal_cost_per_attendee": ["mean", "max"],

    # FMV signals (SPEAKER_001: $3,500 ceiling)
    "speaker_fee_fmv_pct": ["mean", "max"],

    # Attestation quality (ATTEST_001: 80% signed)
    "attendees_signed_pct": ["mean", "min"],
}

# Boolean flag columns → aggregated as sum (count of events where flag = true per HCP).
# Result is an integer count, not a boolean — "how many events had this issue."
EVENT_FLAG_FEATURES: list[str] = [
    "low_attendance_flag",
    "very_low_attendance_flag",
    "cost_per_head_over_limit",
    "high_venue_cost_flag",
    "over_total_cost_ceiling_flag",
    "speaker_fee_over_fmv_flag",
    "repeat_speaker_flag",
    "high_repeat_speaker_flag",
    "rapid_repeat_flag",
    "missing_attestation_flag",
]

# Derived features computed from aggregated values — not direct aggregations.
# Computed in compute_derived_features() after aggregate_to_hcp_level().
DERIVED_FEATURES: list[str] = [
    "total_events_as_speaker",
    "pct_events_low_attendance",
    "pct_events_over_fmv",
    "pct_events_missing_attestation",
    "pct_events_rapid_repeat",
    # Coefficient of variation of risk score (std / mean).
    # High CV = erratic risk pattern (some very high, some low) — also suspicious.
    # Low CV + high mean = consistently risky across all events — also suspicious.
    # Null (single-event speakers, std=0, mean>0 → CV=0) filled with 0.0.
    "event_risk_score_cv",
]

# Flag count column names after aggregation (used in validation and metadata)
FLAG_COUNT_COLUMNS = [f"{col}_sum" for col in EVENT_FLAG_FEATURES]

# Columns NOT scaled:
#   pct_ features — already [0.0, 1.0]
#   flag count features — low-cardinality integers
#   total_events_as_speaker — scaled separately by max
#   event_risk_score_cv — already a dimensionless ratio
DO_NOT_SCALE = set(
    [c for c in DERIVED_FEATURES if c.startswith("pct_")]
    + FLAG_COUNT_COLUMNS
    + ["total_events_as_speaker", "event_risk_score_cv", "hcp_id"]
)


# ─── Data loading ─────────────────────────────────────────────────────────────

def load_event_features() -> pd.DataFrame:
    """
    Connect to DuckDB and read all columns from mart_event_features.

    Expected: 5,241 rows, one per event.
    Returns raw event-level DataFrame.
    Raises on connection or query failure.
    """
    logger.info("Connecting to DuckDB: {}", DUCKDB_PATH)
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
    except Exception as e:
        logger.error("DuckDB connection failed: {}", e)
        raise

    query = f"SELECT * FROM {SOURCE_TABLE}"
    try:
        df = con.execute(query).df()
    except Exception as e:
        logger.error("DuckDB query failed: {}", e)
        con.close()
        raise

    con.close()
    logger.info("Loaded {} rows × {} columns from {}", len(df), len(df.columns), SOURCE_TABLE)

    if len(df) != EXPECTED_EVENTS:
        logger.warning(
            "Unexpected row count: {} (expected {})", len(df), EXPECTED_EVENTS
        )

    return df


# ─── Aggregation ─────────────────────────────────────────────────────────────

def aggregate_to_hcp_level(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate event-level rows to one row per HCP speaker (GROUP BY speaker_hcp_id).

    Applies EVENT_AGG_FEATURES aggregations (mean/max/std/sum/min) and sums
    EVENT_FLAG_FEATURES (count of events where each flag was true).

    Column naming after flattening:
        ("raw_event_risk_score", "mean") → "raw_event_risk_score_mean"
        ("low_attendance_flag", "sum")   → "low_attendance_flag_sum"

    Adds total_events_as_speaker = number of events per HCP.

    Returns DataFrame with speaker_hcp_id as index.
    """
    # Cast boolean flags to int so sum() = count of True events
    for col in EVENT_FLAG_FEATURES:
        if col in df.columns:
            df[col] = df[col].astype("Int64").fillna(0).astype(int)

    # Continuous aggregations
    agg_dict: dict[str, list[str]] = {
        col: aggs
        for col, aggs in EVENT_AGG_FEATURES.items()
        if col in df.columns
    }
    flag_agg_dict = {
        col: ["sum"]
        for col in EVENT_FLAG_FEATURES
        if col in df.columns
    }

    # Total events per HCP (before groupby so we have the count)
    event_counts = df.groupby("speaker_hcp_id")["event_id"].count().rename("total_events_as_speaker")

    # Apply aggregations
    cont_agg = df.groupby("speaker_hcp_id").agg(agg_dict)
    flag_agg = df.groupby("speaker_hcp_id").agg(flag_agg_dict)

    # Flatten multi-level column names: (col, agg) → col_agg
    cont_agg.columns = [f"{col}_{agg}" for col, agg in cont_agg.columns]
    flag_agg.columns = [f"{col}_{agg}" for col, agg in flag_agg.columns]

    # Combine
    agg_df = cont_agg.join(flag_agg).join(event_counts)
    agg_df.index.name = "speaker_hcp_id"

    logger.info(
        "Aggregated {} events → {} HCP speakers × {} columns",
        len(df),
        len(agg_df),
        len(agg_df.columns),
    )
    return agg_df


# ─── Derived features ─────────────────────────────────────────────────────────

def compute_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute HCP-level derived features from aggregated values.

    pct_events_low_attendance:
        low_attendance_flag_sum / total_events_as_speaker
        Fraction of this speaker's events with fewer than 3 attendees (SPEAKER_004).

    pct_events_over_fmv:
        speaker_fee_over_fmv_flag_sum / total_events_as_speaker
        Fraction of events where speaker fee exceeded the $3,500 FMV ceiling.

    pct_events_missing_attestation:
        missing_attestation_flag_sum / total_events_as_speaker
        Fraction of events with < 80% attendees signed (ATTEST_001).

    pct_events_rapid_repeat:
        rapid_repeat_flag_sum / total_events_as_speaker
        Fraction of events occurring < 30 days after the speaker's prior event.

    event_risk_score_cv:
        raw_event_risk_score_std / raw_event_risk_score_mean
        Coefficient of variation — measures consistency of risk across events.
        High CV = erratic pattern (some very high, some low) — suspicious.
        Low CV + high mean = consistently risky across all events — also suspicious.
        Null for single-event speakers (std=0) → filled with 0.0.

    All division uses np.where(total > 0, num/total, 0.0) to avoid div/zero.
    """
    n = df["total_events_as_speaker"]

    df["pct_events_low_attendance"] = np.where(
        n > 0, df["low_attendance_flag_sum"] / n, 0.0
    )
    df["pct_events_over_fmv"] = np.where(
        n > 0, df["speaker_fee_over_fmv_flag_sum"] / n, 0.0
    )
    df["pct_events_missing_attestation"] = np.where(
        n > 0, df["missing_attestation_flag_sum"] / n, 0.0
    )
    df["pct_events_rapid_repeat"] = np.where(
        n > 0, df["rapid_repeat_flag_sum"] / n, 0.0
    )

    # CV: std / mean — 0.0 for single-event speakers (std=0) or mean=0
    df["event_risk_score_cv"] = np.where(
        df["raw_event_risk_score_mean"] > 0,
        df["raw_event_risk_score_std"] / df["raw_event_risk_score_mean"],
        0.0,
    )

    logger.info("Derived features computed: {}", DERIVED_FEATURES)
    logger.debug(
        "pct_events_low_attendance: mean={:.3f}, max={:.3f}",
        df["pct_events_low_attendance"].mean(),
        df["pct_events_low_attendance"].max(),
    )
    logger.debug(
        "event_risk_score_cv: mean={:.3f}, max={:.3f}",
        df["event_risk_score_cv"].mean(),
        df["event_risk_score_cv"].max(),
    )
    return df


# ─── Null handling ─────────────────────────────────────────────────────────────

def handle_nulls(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply null fill strategy per feature type.

    std aggregations (e.g. raw_event_risk_score_std):
        Fill with 0.0 — only one event means std is undefined, not a risk signal.

    min aggregations (e.g. attendee_count_min, attendees_signed_pct_min):
        Fill with 0.0 — no events means minimum is undefined.

    pct_ derived features:
        Fill with 0.0 — no events means no violations.

    event_risk_score_cv:
        Fill with 0.0 — CV undefined for single-event speakers.

    All remaining nulls:
        Fill with 0.0 as safe default.
    """
    null_counts_before = df.isnull().sum()
    filled_cols = null_counts_before[null_counts_before > 0]
    if not filled_cols.empty:
        logger.info("Nulls before fill:\n{}", filled_cols.to_string())

    # std columns
    std_cols = [c for c in df.columns if c.endswith("_std")]
    for col in std_cols:
        df[col] = df[col].fillna(0.0)

    # min columns
    min_cols = [c for c in df.columns if c.endswith("_min")]
    for col in min_cols:
        df[col] = df[col].fillna(0.0)

    # pct_ derived features
    pct_cols = [c for c in df.columns if c.startswith("pct_")]
    for col in pct_cols:
        df[col] = df[col].fillna(0.0)

    # CV
    if "event_risk_score_cv" in df.columns:
        df["event_risk_score_cv"] = df["event_risk_score_cv"].fillna(0.0)

    # Catch-all
    df = df.fillna(0.0)

    remaining_nulls = df.isnull().sum().sum()
    logger.info(
        "Null fill complete: {} cols had nulls → {} remaining",
        len(filled_cols),
        remaining_nulls,
    )
    return df


# ─── Scaling ──────────────────────────────────────────────────────────────────

def scale_features(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Apply RobustScaler to continuous cost and risk aggregations.

    RobustScaler uses median and IQR — resistant to the extreme outliers
    present in compliance data (e.g. events at 2x FMV ceiling).

    NOT scaled:
        pct_ features — already in [0.0, 1.0]
        flag count features (_sum columns) — low-cardinality integers
        event_risk_score_cv — already a dimensionless ratio
        total_events_as_speaker — scaled separately by max (relative count)
        hcp_id — identity column

    total_events_as_speaker is normalized to [0, 1] by dividing by its max
    value so it's on the same scale as other features without distortion.

    Returns:
        scaled DataFrame
        scaler_params dict: {column: {center: float, scale: float}}
    """
    # Columns eligible for RobustScaler: all numeric, not in DO_NOT_SCALE
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    scale_cols = [c for c in numeric_cols if c not in DO_NOT_SCALE]

    scaler = RobustScaler()
    df[scale_cols] = scaler.fit_transform(df[scale_cols])

    scaler_params: dict = {
        col: {
            "center": float(scaler.center_[i]),
            "scale":  float(scaler.scale_[i]),
        }
        for i, col in enumerate(scale_cols)
    }

    # total_events_as_speaker: normalize to [0, 1] by dividing by max
    if "total_events_as_speaker" in df.columns:
        max_events = df["total_events_as_speaker"].max()
        if max_events > 0:
            df["total_events_as_speaker"] = df["total_events_as_speaker"] / max_events
            scaler_params["total_events_as_speaker"] = {
                "center": 0.0,
                "scale": float(max_events),
            }
        else:
            scaler_params["total_events_as_speaker"] = {"center": 0.0, "scale": 1.0}

    logger.info(
        "Scaling: RobustScaler on {} columns + total_events_as_speaker /max",
        len(scale_cols),
    )
    return df, scaler_params


# ─── Identity columns ─────────────────────────────────────────────────────────

def add_identity_columns(scaled_df: pd.DataFrame) -> pd.DataFrame:
    """
    Restore speaker_hcp_id as hcp_id column (join key for feature_store.py).

    The index is speaker_hcp_id from the GROUP BY aggregation.
    Rename to hcp_id to match the key in hcp_spend_feature_matrix.parquet.
    Cast to str to guarantee format consistency.
    """
    scaled_df = scaled_df.reset_index()
    scaled_df = scaled_df.rename(columns={"speaker_hcp_id": "hcp_id"})
    scaled_df["hcp_id"] = scaled_df["hcp_id"].astype(str)
    logger.debug("Identity column restored: speaker_hcp_id → hcp_id")
    return scaled_df


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_output(df: pd.DataFrame) -> bool:
    """
    Run basic validation checks on the output feature matrix.

    Checks:
        Row count <= 1,354 (only HCP speakers have events)
        No nulls in any column
        No infinite values
        pct_ features all in [0.0, 1.0]
        total_events_as_speaker >= 0 (was >0 before /max normalization)

    Raises ValueError if any check fails.
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

    # Row count
    _check(
        "Row count <= expected speakers",
        len(df) <= EXPECTED_SPEAKERS,
        f"{len(df)} rows (expected <= {EXPECTED_SPEAKERS})",
    )

    # No nulls
    null_total = df.isnull().sum().sum()
    _check("No nulls", null_total == 0, f"{null_total} nulls found")

    # No infinities in numeric columns
    numeric_df = df.select_dtypes(include="number")
    inf_count = np.isinf(numeric_df).sum().sum()
    _check("No infinite values", inf_count == 0, f"{inf_count} inf values found")

    # pct_ features in [0.0, 1.0]
    pct_cols = [c for c in df.columns if c.startswith("pct_")]
    bad_pct = []
    for col in pct_cols:
        vmin, vmax = df[col].min(), df[col].max()
        if vmin < -1e-9 or vmax > 1.0 + 1e-9:
            bad_pct.append(f"{col}: [{vmin:.4f}, {vmax:.4f}]")
    _check("pct_ features in [0, 1]", len(bad_pct) == 0, "; ".join(bad_pct))

    # total_events_as_speaker >= 0 (after /max normalization)
    if "total_events_as_speaker" in df.columns:
        _check(
            "total_events_as_speaker >= 0",
            (df["total_events_as_speaker"] >= 0).all(),
            f"min={df['total_events_as_speaker'].min():.4f}",
        )

    logger.info("Validation: {}/{} checks passed", checks_passed, checks_passed + checks_failed)

    if checks_failed > 0:
        raise ValueError(f"Validation failed: {checks_failed} check(s) failed — see logs above")

    return True


# ─── Output ───────────────────────────────────────────────────────────────────

def save_outputs(df: pd.DataFrame, scaler_params: dict, source_rows: int) -> dict:
    """
    Save event feature matrix as parquet and metadata as JSON.

    Returns dict of output paths.
    """
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    matrix_path   = out_dir / "event_feature_matrix.parquet"
    metadata_path = out_dir / "event_feature_metadata.json"

    df.to_parquet(matrix_path, index=False)
    logger.info("Saved feature matrix: {} ({} rows)", matrix_path, len(df))

    # Build column lists for metadata
    feature_cols = [
        c for c in df.columns
        if c not in ("hcp_id",)
        and c not in [f"{col}_sum" for col in EVENT_FLAG_FEATURES]
        and c not in DERIVED_FEATURES
    ]
    flag_count_cols = [c for c in df.columns if c in FLAG_COUNT_COLUMNS]
    derived_cols    = [c for c in df.columns if c in DERIVED_FEATURES]

    metadata = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "source_table":   f"duckdb: {SOURCE_TABLE}",
        "source_rows":    source_rows,
        "hcp_speakers":   len(df),
        "feature_columns":    feature_cols,
        "flag_count_columns": flag_count_cols,
        "derived_columns":    derived_cols,
        "identity_columns":   ["hcp_id"],
        "scaler":             "RobustScaler",
        "scaler_params":      scaler_params,
        "aggregation_note": (
            "Event-level features aggregated to HCP speaker level. "
            f"{len(df)} HCPs have events. "
            "Remaining HCPs joined with 0-fill in feature_store.py (Task 2.7)."
        ),
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
    logger.info("event_features.py — Phase 2 event feature engineering")
    logger.info("Config: DUCKDB_PATH={}", DUCKDB_PATH)
    logger.info("=" * 60)

    # 1. Load raw event data
    raw_df = load_event_features()
    source_rows = len(raw_df)

    # 2. Aggregate to HCP level
    agg_df = aggregate_to_hcp_level(raw_df)

    # 3. Derived features
    derived_df = compute_derived_features(agg_df)

    # 4. Null handling
    clean_df = handle_nulls(derived_df)

    # 5. Scaling
    scaled_df, scaler_params = scale_features(clean_df)

    # 6. Restore identity column
    final_df = add_identity_columns(scaled_df)

    # 7. Validate
    validate_output(final_df)

    # 8. Save
    output_paths = save_outputs(final_df, scaler_params, source_rows)

    elapsed = time.time() - start
    feature_cols    = [c for c in final_df.columns if c not in ("hcp_id",) and c not in FLAG_COUNT_COLUMNS and c not in DERIVED_FEATURES]
    flag_count_cols = [c for c in final_df.columns if c in FLAG_COUNT_COLUMNS]
    derived_cols    = [c for c in final_df.columns if c in DERIVED_FEATURES]

    logger.info("")
    logger.info("─" * 60)
    logger.info("Complete.")
    logger.info("  Source events:       {}", source_rows)
    logger.info("  HCP speakers:        {}", len(final_df))
    logger.info("  Feature columns:     {}", len(feature_cols))
    logger.info("  Flag count columns:  {}", len(flag_count_cols))
    logger.info("  Derived features:    {}", len(derived_cols))
    logger.info("  Output:              {}/", OUTPUT_DIR)
    logger.info("  Time taken:          {:.1f}s", elapsed)
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
