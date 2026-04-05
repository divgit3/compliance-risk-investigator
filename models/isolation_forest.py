"""
isolation_forest.py
Phase 2 — Isolation Forest anomaly detection

Reads feature_store.parquet (97,011 HCPs × N features) produced by
feature_store.py and fits a sklearn IsolationForest to score each HCP
for statistical anomalousness relative to peers.

This is the second of two anomaly detection approaches:
  1. Rule-based flags (rule_based_flags.py) — catches KNOWN rule violations
  2. Isolation Forest (this module)          — catches UNKNOWN statistical patterns

Both feed into scorer.py (Task 2.10), which combines them into a unified
0–100 risk score per HCP.

Design choices:
  contamination = 0.10  — expected ~10% statistical outliers in compliance data
  n_estimators  = 200   — more trees → more stable scores vs default 100
  max_samples   = "auto" — min(256, n_samples) — sklearn default
  max_features  = 1.0   — all features per tree; compliance features are all relevant
  random_state  = 42    — reproducibility

Score transformation:
  decision_function() returns raw anomaly scores where more negative = more anomalous.
  These are negated and min-max scaled to [0, 100] so higher = more anomalous.
  Threshold: raw decision_function() < 0 → sklearn-labeled "outlier" (predict = -1).

hcp_id alignment note:
  feature_store.parquet excludes hcp_id (identity column dropped before ML).
  hcp_ids are loaded from mart_benchmark (DuckDB) ORDER BY hcp_id ASC.
  feature_store.parquet was built from the same 97,011-HCP spine in the same
  row order. Row count equality is validated before positional attachment.
  See align_hcp_ids() for details.

Prerequisite:
  Run features/feature_store.py first (Athena + DuckDB required).
  feature_store.parquet must exist at features/outputs/feature_store.parquet.

Usage:
    python3 models/isolation_forest.py
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
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

# ─── Environment ─────────────────────────────────────────────────────────────
load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
FEATURE_STORE_PATH = "features/outputs/feature_store.parquet"
METADATA_PATH      = "features/outputs/feature_store_metadata.json"
DUCKDB_PATH        = "data/processed/compliance.duckdb"
OUTPUT_DIR         = "models/outputs"

# IsolationForest hyperparameters
IF_CONTAMINATION  = 0.10   # expected fraction of statistical outliers
IF_N_ESTIMATORS   = 200    # number of trees — more stable than default 100
IF_MAX_SAMPLES    = "auto" # min(256, n_samples) — sklearn default
IF_MAX_FEATURES   = 1.0    # fraction of features per tree — use all
IF_RANDOM_STATE   = 42     # reproducibility

# Score output range
SCORE_MIN = 0.0
SCORE_MAX = 100.0

# Validation bounds
EXPECTED_ROWS            = 97_011
OUTLIER_RATE_MIN         = 0.05   # at least 5% flagged anomalous
OUTLIER_RATE_MAX         = 0.20   # at most 20% flagged anomalous
SCORE_SPREAD_MIN         = 29.0   # p95 - p5 must be >= 30 (sufficient separation)


# ─── Load feature store ───────────────────────────────────────────────────────

def load_feature_store() -> tuple[pd.DataFrame, dict]:
    """
    Load feature_store.parquet and its metadata.

    Returns:
      X_df:     DataFrame of all numeric feature columns (no hcp_id, no GT)
      metadata: dict loaded from feature_store_metadata.json

    Raises FileNotFoundError if feature_store.parquet is absent.
    Run features/feature_store.py first (Athena required).
    """
    fs_path   = Path(FEATURE_STORE_PATH)
    meta_path = Path(METADATA_PATH)

    if not fs_path.exists():
        raise FileNotFoundError(
            f"Feature store not found at {fs_path}. "
            "Run features/feature_store.py first (Athena + DuckDB required)."
        )

    X_df = pd.read_parquet(fs_path)
    logger.info(
        "Feature store loaded: {} rows × {} columns",
        len(X_df),
        len(X_df.columns),
    )

    metadata: dict = {}
    if meta_path.exists():
        with open(meta_path) as f:
            metadata = json.load(f)
        logger.debug("Feature store metadata loaded from {}", meta_path)
    else:
        logger.warning("feature_store_metadata.json not found — proceeding without it")

    return X_df, metadata


# ─── Load hcp_ids ─────────────────────────────────────────────────────────────

def load_hcp_ids() -> pd.Series:
    """
    Load hcp_ids from mart_benchmark (DuckDB) in a consistent ORDER BY hcp_id.

    feature_store.parquet was built from the same 97,011-HCP spine in the same
    row order (both from DuckDB mart_benchmark as the primary spine on dev, or
    from mart_hcp_spend_features as the Athena spine on prod). On DuckDB dev
    the spine is mart_benchmark; on Athena prod the spend matrix spine is used.

    Row count equality is validated in align_hcp_ids() before positional join.

    Returns:
        Series of hcp_id strings, ordered consistently with feature_store rows.
    """
    logger.info("Loading hcp_ids from mart_benchmark (DuckDB)")
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
    except Exception as e:
        logger.error("DuckDB connection failed: {}", e)
        raise

    df = con.execute(
        "SELECT hcp_id FROM mart_benchmark ORDER BY hcp_id ASC"
    ).df()
    con.close()

    hcp_ids = df["hcp_id"].astype(str)
    logger.info("hcp_ids loaded: {} HCPs", len(hcp_ids))
    return hcp_ids


# ─── Align hcp_ids with feature matrix ───────────────────────────────────────

def align_hcp_ids(X_df: pd.DataFrame, hcp_ids: pd.Series) -> pd.DataFrame:
    """
    Attach hcp_ids to the feature matrix by positional alignment.

    Validates:
      - len(X_df) == len(hcp_ids) (must be 97,011)
      - len(X_df) == EXPECTED_ROWS

    Returns DataFrame with hcp_id as first column, feature columns after.

    Note: positional alignment assumes feature_store.parquet was built from
    the same DuckDB spine in consistent row order. Mismatch in row count
    raises ValueError. On DuckDB dev feature_store.parquet requires Athena,
    so this function is only called in prod/full-pipeline runs.
    """
    n_features = len(X_df)
    n_ids      = len(hcp_ids)

    if n_features != EXPECTED_ROWS:
        raise ValueError(
            f"Feature store has {n_features} rows — expected {EXPECTED_ROWS}. "
            "Regenerate features/outputs/feature_store.parquet."
        )
    if n_ids != EXPECTED_ROWS:
        raise ValueError(
            f"DuckDB mart_benchmark has {n_ids} HCPs — expected {EXPECTED_ROWS}. "
            "DuckDB schema may have changed."
        )

    result = X_df.copy()
    result.insert(0, "hcp_id", hcp_ids.values)
    logger.info(
        "hcp_ids attached: {} rows — positional alignment from DuckDB spine",
        len(result),
    )
    return result


# ─── Train Isolation Forest ───────────────────────────────────────────────────

def train_isolation_forest(X: np.ndarray) -> tuple[IsolationForest, dict]:
    """
    Fit IsolationForest on the full feature matrix.

    Uses all 97,011 HCPs for unsupervised training — no train/test split.
    Isolation Forest is a transductive anomaly detector: it fits and scores
    the same dataset. Ground truth labels are never used during fitting
    (unsupervised). They are only used in test_anomaly_models.py for evaluation.

    Hyperparameters:
      contamination = 0.10 — calibrated to ~10% expected compliance outliers
      n_estimators  = 200  — more stable than default 100 for 97K rows
      max_samples   = "auto" — min(256, n_samples)
      max_features  = 1.0  — all features per tree
      random_state  = 42   — reproducibility

    Returns:
      clf:    fitted IsolationForest
      params: dict of hyperparameters for metadata
    """
    params = {
        "n_estimators":  IF_N_ESTIMATORS,
        "contamination": IF_CONTAMINATION,
        "max_samples":   IF_MAX_SAMPLES,
        "max_features":  IF_MAX_FEATURES,
        "random_state":  IF_RANDOM_STATE,
        "n_features":    X.shape[1],
        "n_samples":     X.shape[0],
    }

    logger.info(
        "Fitting IsolationForest: {} trees, contamination={}, {} samples × {} features",
        IF_N_ESTIMATORS,
        IF_CONTAMINATION,
        X.shape[0],
        X.shape[1],
    )

    clf = IsolationForest(
        n_estimators  = IF_N_ESTIMATORS,
        contamination = IF_CONTAMINATION,
        max_samples   = IF_MAX_SAMPLES,
        max_features  = IF_MAX_FEATURES,
        random_state  = IF_RANDOM_STATE,
        n_jobs        = -1,  # use all CPU cores
    )
    clf.fit(X)

    logger.info("IsolationForest fit complete")
    return clf, params


# ─── Compute anomaly scores ───────────────────────────────────────────────────

def compute_anomaly_scores(clf: IsolationForest, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute anomaly scores from the fitted IsolationForest.

    sklearn decision_function():
      - Returns raw anomaly score per sample
      - More negative = more anomalous (deeper in isolation trees)
      - Boundary: < 0 → sklearn labels as outlier (predict = -1)
      - Typical range: [-0.5, 0.5]

    Score transformation to [0, 100]:
      1. Negate raw score: more anomalous → more positive
      2. Min-max scale to [0, 100]
      → anomaly_score = 0 means most normal HCP in the population
      → anomaly_score = 100 means most anomalous HCP in the population

    Returns:
      raw_scores:        raw decision_function() values (negative = anomalous)
      labels:            sklearn predict() labels: -1 (outlier) or 1 (inlier)
      anomaly_scores:    [0, 100] scaled scores (100 = most anomalous)
    """
    raw_scores = clf.decision_function(X)  # shape: (n_samples,)
    labels     = clf.predict(X)            # -1 or 1

    # Negate: more anomalous → larger positive value
    inverted   = -raw_scores

    # Min-max scale to [0, 100]
    min_s      = inverted.min()
    max_s      = inverted.max()
    spread     = max_s - min_s

    if spread < 1e-9:
        logger.warning(
            "Score spread near zero ({:.6f}) — all HCPs scored identically. "
            "Check feature_store.parquet for constant features.",
            spread,
        )
        anomaly_scores = np.full(len(raw_scores), 50.0)
    else:
        anomaly_scores = SCORE_MIN + (SCORE_MAX - SCORE_MIN) * (inverted - min_s) / spread

    logger.info(
        "Scores computed: min={:.2f}, p25={:.2f}, median={:.2f}, "
        "p75={:.2f}, p95={:.2f}, max={:.2f}",
        float(np.min(anomaly_scores)),
        float(np.percentile(anomaly_scores, 25)),
        float(np.median(anomaly_scores)),
        float(np.percentile(anomaly_scores, 75)),
        float(np.percentile(anomaly_scores, 95)),
        float(np.max(anomaly_scores)),
    )
    return raw_scores, labels, anomaly_scores


# ─── Build output DataFrame ───────────────────────────────────────────────────

def build_scores_df(
    aligned_df: pd.DataFrame,
    raw_scores: np.ndarray,
    labels: np.ndarray,
    anomaly_scores: np.ndarray,
) -> pd.DataFrame:
    """
    Build output DataFrame: hcp_id + all score columns.

    Columns:
      hcp_id              — string HCP identifier
      anomaly_score       — [0, 100] scaled IF score (100 = most anomalous)
      if_raw_score        — raw decision_function() value (negative = more anomalous)
      if_is_outlier       — 1 if sklearn predict = -1 (outlier), else 0
      anomaly_percentile  — percentile rank of anomaly_score within the population
                            (0.0 = most normal, 1.0 = most anomalous)

    scorer.py (Task 2.10) consumes:
      hcp_id + anomaly_score (primary) + if_is_outlier (binary signal)
    """
    scores_df = pd.DataFrame({
        "hcp_id":             aligned_df["hcp_id"].values,
        "anomaly_score":      anomaly_scores.astype(np.float32),
        "if_raw_score":       raw_scores.astype(np.float32),
        "if_is_outlier":      (labels == -1).astype(np.int8),
        "anomaly_percentile": pd.Series(anomaly_scores).rank(pct=True, method="min").values.astype(np.float32),
    })
    return scores_df


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_scores(scores_df: pd.DataFrame) -> bool:
    """
    Validate IF score output.

    Checks:
      - Row count == 97,011
      - No nulls in anomaly_score
      - anomaly_score range in [0, 100]
      - Outlier rate in [OUTLIER_RATE_MIN, OUTLIER_RATE_MAX]
      - Score spread (p95 - p5) >= SCORE_SPREAD_MIN — sufficient discrimination
      - hcp_id unique (no duplicate HCPs)

    Raises ValueError on critical failures.
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

    _check("Row count", len(scores_df) == EXPECTED_ROWS,
           f"{len(scores_df)} (expected {EXPECTED_ROWS})")

    null_count = scores_df["anomaly_score"].isnull().sum()
    _check("No nulls in anomaly_score", null_count == 0,
           f"{null_count} nulls")

    score_range_ok = (
        scores_df["anomaly_score"].min() >= SCORE_MIN
        and scores_df["anomaly_score"].max() <= SCORE_MAX
    )
    _check("anomaly_score in [0, 100]", score_range_ok,
           f"range [{scores_df['anomaly_score'].min():.2f}, {scores_df['anomaly_score'].max():.2f}]")

    outlier_rate = scores_df["if_is_outlier"].mean()
    _check(
        f"Outlier rate in [{OUTLIER_RATE_MIN}, {OUTLIER_RATE_MAX}]",
        OUTLIER_RATE_MIN <= outlier_rate <= OUTLIER_RATE_MAX,
        f"{outlier_rate:.3f} ({outlier_rate * 100:.1f}% labeled as outliers)",
    )

    spread = float(np.percentile(scores_df["anomaly_score"], 95) - np.percentile(scores_df["anomaly_score"], 5))
    _check(
        f"Score spread (p95 - p5) >= {SCORE_SPREAD_MIN}",
        spread >= SCORE_SPREAD_MIN,
        f"spread = {spread:.2f}",
    )

    n_unique = scores_df["hcp_id"].nunique()
    _check("hcp_id unique", n_unique == len(scores_df),
           f"{n_unique} unique of {len(scores_df)}")

    logger.info(
        "Validation: {}/{} checks passed",
        checks_passed,
        checks_passed + checks_failed,
    )
    if checks_failed > 0:
        raise ValueError(
            f"IF score validation failed: {checks_failed} check(s) — see logs above"
        )
    return True


# ─── SHAP computation ─────────────────────────────────────────────────────────

def compute_shap_values(
    clf: IsolationForest,
    X: np.ndarray,
    feature_cols: list[str],
    hcp_ids: pd.Series,
) -> bool:
    """
    Compute per-HCP SHAP values using shap.TreeExplainer and save to
    models/outputs/shap_values.parquet.

    Columns: hcp_id + one column per feature (99 cols). Values are raw SHAP
    values (not absolute): positive = pushes anomaly score up, negative = down.

    Returns True on success, False on failure (never crashes the pipeline).

    Notes:
      - TreeExplainer supports sklearn IsolationForest natively.
      - check_additivity=False avoids an O(n) verification pass — safe for IF.
      - 97K rows × 99 features with 200 trees: ~30-90s depending on hardware.
    """
    out_path = Path(OUTPUT_DIR) / "shap_values.parquet"
    try:
        import shap

        logger.info("Computing SHAP values with TreeExplainer ({} samples × {} features)…",
                    X.shape[0], X.shape[1])
        t0 = time.time()

        explainer   = shap.TreeExplainer(clf)
        shap_matrix = explainer.shap_values(X, check_additivity=False)

        elapsed = time.time() - t0
        logger.info("SHAP computation complete in {:.1f}s", elapsed)

        # shap_matrix shape: (n_samples, n_features) for IsolationForest
        shap_df = pd.DataFrame(shap_matrix, columns=feature_cols)
        shap_df.insert(0, "hcp_id", hcp_ids.values)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        shap_df.to_parquet(out_path, index=False)

        logger.info(
            "Saved shap_values.parquet: {} rows × {} feature cols  ({})",
            len(shap_df),
            len(feature_cols),
            out_path,
        )

        # Quick sanity: mean |SHAP| per feature (top 5)
        mean_abs = shap_df[feature_cols].abs().mean().sort_values(ascending=False)
        logger.info("Top 5 features by mean |SHAP|:\n{}",
                    mean_abs.head(5).to_string())
        return True

    except ImportError:
        logger.warning("shap package not installed — skipping SHAP computation")
        return False
    except Exception as exc:
        logger.warning("SHAP computation failed: {} — skipping (IF scores unaffected)", exc)
        return False


# ─── Save outputs ─────────────────────────────────────────────────────────────

def save_outputs(
    scores_df: pd.DataFrame,
    clf_params: dict,
    fit_duration_s: float,
    score_stats: dict,
) -> dict:
    """
    Save IF scores and metadata.

    if_scores.parquet:
        hcp_id + anomaly_score + if_raw_score + if_is_outlier + anomaly_percentile
        Read by scorer.py (Task 2.10) for unified risk scoring.

    if_metadata.json:
        Model hyperparameters, score distribution, timing, feature count.

    Returns dict of output paths.
    """
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    scores_path = out_dir / "if_scores.parquet"
    meta_path   = out_dir / "if_metadata.json"

    scores_df.to_parquet(scores_path, index=False)
    logger.info("Saved IF scores: {} ({} rows)", scores_path, len(scores_df))

    metadata = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "model":           "IsolationForest",
        "library":         "sklearn",
        "hyperparameters": clf_params,
        "fit_duration_s":  round(fit_duration_s, 2),
        "score_distribution": score_stats,
        "output_columns":  scores_df.columns.tolist(),
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved IF metadata: {}", meta_path)

    return {
        "if_scores":  str(scores_path),
        "if_metadata": str(meta_path),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info("isolation_forest.py — Phase 2 anomaly detection")
    logger.info("=" * 60)

    # 1. Load feature matrix
    X_df, fs_metadata = load_feature_store()

    # 2. Load hcp_ids for alignment
    hcp_ids = load_hcp_ids()

    # 3. Attach hcp_ids (positional alignment with row count validation)
    aligned_df = align_hcp_ids(X_df, hcp_ids)

    # 4. Extract pure numpy feature matrix (no hcp_id column)
    feature_cols = [c for c in aligned_df.columns if c != "hcp_id"]
    X = aligned_df[feature_cols].values.astype(np.float64)

    logger.info(
        "Feature matrix ready: {} rows × {} features",
        X.shape[0],
        X.shape[1],
    )

    # 5. Fit IsolationForest
    fit_start = time.time()
    clf, clf_params = train_isolation_forest(X)
    fit_duration = time.time() - fit_start
    logger.info("Fit completed in {:.2f}s", fit_duration)

    # 6. Compute anomaly scores
    raw_scores, labels, anomaly_scores = compute_anomaly_scores(clf, X)

    # 7. Build output DataFrame
    scores_df = build_scores_df(aligned_df, raw_scores, labels, anomaly_scores)

    # 8. Collect score stats for metadata
    score_stats = {
        "n_outliers":       int((labels == -1).sum()),
        "outlier_rate":     float((labels == -1).mean()),
        "anomaly_score_mean":   float(np.mean(anomaly_scores)),
        "anomaly_score_median": float(np.median(anomaly_scores)),
        "anomaly_score_std":    float(np.std(anomaly_scores)),
        "anomaly_score_p25":    float(np.percentile(anomaly_scores, 25)),
        "anomaly_score_p75":    float(np.percentile(anomaly_scores, 75)),
        "anomaly_score_p90":    float(np.percentile(anomaly_scores, 90)),
        "anomaly_score_p95":    float(np.percentile(anomaly_scores, 95)),
        "anomaly_score_p99":    float(np.percentile(anomaly_scores, 99)),
        "raw_score_min":        float(raw_scores.min()),
        "raw_score_max":        float(raw_scores.max()),
        "raw_score_mean":       float(raw_scores.mean()),
    }

    # 9. Validate
    validate_scores(scores_df)

    # 10. Save
    output_paths = save_outputs(
        scores_df,
        clf_params,
        fit_duration,
        score_stats,
    )

    # 11. SHAP values (best-effort — never blocks the pipeline)
    compute_shap_values(clf, X, feature_cols, aligned_df["hcp_id"])

    elapsed = time.time() - start

    logger.info("")
    logger.info("─" * 60)
    logger.info("Complete.")
    logger.info("  Total HCPs scored:         {}", len(scores_df))
    logger.info("  Features used:             {}", X.shape[1])
    logger.info("  Outliers flagged:          {} ({:.1f}%)",
                score_stats["n_outliers"],
                score_stats["outlier_rate"] * 100)
    logger.info("  Anomaly score median:      {:.2f}",
                score_stats["anomaly_score_median"])
    logger.info("  Anomaly score p95:         {:.2f}",
                score_stats["anomaly_score_p95"])
    logger.info("  Fit duration:              {:.2f}s", fit_duration)
    logger.info("  Total time:                {:.1f}s", elapsed)
    logger.info("  Output: {}/", OUTPUT_DIR)
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
