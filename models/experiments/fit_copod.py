# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
Fit COPOD on the same 97,011-HCP feature matrix used by IsolationForest.

Outputs (models/outputs/):
  copod_scores.parquet             — hcp_id + copod_score (higher = more anomalous)
  copod_feature_percentiles.parquet — hcp_id + one tail-probability column per feature
                                      values in [0, 0.5]; smaller = more extreme on that feature

Usage:
    python3 models/experiments/fit_copod.py
"""

import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from loguru import logger
from pyod.models.copod import COPOD

FEATURE_STORE_PATH = "features/outputs/feature_store.parquet"
DUCKDB_PATH        = "data/processed/compliance.duckdb"
OUTPUT_DIR         = Path("models/outputs")
EXPECTED_ROWS      = 97_011


def load_feature_matrix() -> tuple[np.ndarray, list[str], pd.Series]:
    """Load feature_store.parquet and align hcp_ids from DuckDB, same as isolation_forest.py."""
    X_df = pd.read_parquet(FEATURE_STORE_PATH)
    feature_cols = X_df.columns.tolist()
    logger.info("Feature store loaded: {} rows × {} cols", len(X_df), len(feature_cols))

    assert len(X_df) == EXPECTED_ROWS, (
        f"Feature store has {len(X_df)} rows — expected {EXPECTED_ROWS}. "
        "Regenerate features/outputs/feature_store.parquet."
    )

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    hcp_ids = con.execute("SELECT hcp_id FROM mart_benchmark ORDER BY hcp_id ASC").df()["hcp_id"].astype(str)
    con.close()

    assert len(hcp_ids) == EXPECTED_ROWS, (
        f"DuckDB mart_benchmark has {len(hcp_ids)} HCPs — expected {EXPECTED_ROWS}."
    )

    return X_df.values.astype(np.float64), feature_cols, hcp_ids


def fit_copod(X: np.ndarray) -> COPOD:
    logger.info("Fitting COPOD: {} samples × {} features", X.shape[0], X.shape[1])
    t0 = time.time()
    clf = COPOD(n_jobs=-1)
    clf.fit(X)
    logger.info("COPOD fit complete in {:.1f}s", time.time() - t0)
    return clf


def compute_feature_tail_probs(X: np.ndarray, feature_cols: list[str], hcp_ids: pd.Series) -> pd.DataFrame:
    """
    Compute per-feature tail probabilities: min(ecdf(x), 1 - ecdf(x)) ∈ [0, 0.5].
    Smaller = more extreme (more anomalous) on that feature. Matches COPOD's internal logic.
    """
    n = X.shape[0]
    tail_probs = np.empty_like(X, dtype=np.float32)
    for j in range(X.shape[1]):
        col = X[:, j]
        ranks = col.argsort().argsort() + 1  # 1-indexed ranks (ties: first occurrence wins)
        ecdf = ranks / n
        tail_probs[:, j] = np.minimum(ecdf, 1.0 - ecdf).astype(np.float32)

    df = pd.DataFrame(tail_probs, columns=feature_cols)
    df.insert(0, "hcp_id", hcp_ids.values)
    return df


def main() -> None:
    X, feature_cols, hcp_ids = load_feature_matrix()

    clf = fit_copod(X)

    copod_scores = clf.decision_scores_  # higher = more anomalous

    scores_df = pd.DataFrame({
        "hcp_id":      hcp_ids.values,
        "copod_score": copod_scores.astype(np.float32),
    })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    scores_path = OUTPUT_DIR / "copod_scores.parquet"
    scores_df.to_parquet(scores_path, index=False)
    logger.info(
        "Saved copod_scores.parquet: {} rows  min={:.3f} median={:.3f} max={:.3f}",
        len(scores_df),
        float(np.min(copod_scores)),
        float(np.median(copod_scores)),
        float(np.max(copod_scores)),
    )

    logger.info("Computing per-feature tail probabilities…")
    t0 = time.time()
    percentiles_df = compute_feature_tail_probs(X, feature_cols, hcp_ids)
    percentiles_path = OUTPUT_DIR / "copod_feature_percentiles.parquet"
    percentiles_df.to_parquet(percentiles_path, index=False)
    logger.info(
        "Saved copod_feature_percentiles.parquet: {} rows × {} feature cols  ({:.1f}s)",
        len(percentiles_df),
        len(feature_cols),
        time.time() - t0,
    )


if __name__ == "__main__":
    main()
