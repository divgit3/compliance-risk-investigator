# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
Surface feature list and SHAP importance for the methodology doc.

Inputs:
  features/outputs/feature_store.parquet   — feature matrix (no hcp_id column)
  models/outputs/shap_values.parquet       — hcp_id + one SHAP column per feature
  models/outputs/if_scores.parquet         — hcp_id + anomaly_score (for top-1% slice)

Outputs (models/experiments/outputs/):
  shap_global_importance.parquet   — feature, mean_abs_shap, pct_of_total
  shap_top1pct_importance.parquet  — feature, mean_abs_shap_top1pct
"""

from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_STORE_PATH = "features/outputs/feature_store.parquet"
SHAP_PATH          = "models/outputs/shap_values.parquet"
IF_SCORES_PATH     = "models/outputs/if_scores.parquet"
OUTPUT_DIR         = Path("models/experiments/outputs")

NON_FEATURE_COLS = {"hcp_id"}


def load_feature_cols() -> list[str]:
    """Return feature column names from feature_store.parquet."""
    df = pd.read_parquet(FEATURE_STORE_PATH)
    cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    print(f"\nFeature store: {len(df)} rows × {len(df.columns)} columns total")
    print(f"Feature columns ({len(cols)}):")
    for i, c in enumerate(cols, 1):
        print(f"  {i:3d}. {c}")
    return cols


def load_shap(feature_cols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Load shap_values.parquet and intersect columns with feature_cols."""
    shap_df = pd.read_parquet(SHAP_PATH)
    shap_feature_cols = [c for c in shap_df.columns if c in set(feature_cols)]

    missing = [c for c in feature_cols if c not in set(shap_feature_cols)]
    if missing:
        print(f"\nWARNING: {len(missing)} feature(s) missing from SHAP "
              f"(SHAP may have been computed on a different feature set):")
        for c in missing:
            print(f"  - {c}")

    assert shap_feature_cols, "SHAP intersection with feature_cols is empty — check file paths."
    print(f"\nSHAP: {len(shap_df)} rows × {len(shap_feature_cols)} feature cols matched")
    return shap_df, shap_feature_cols


def global_importance(shap_df: pd.DataFrame, shap_feature_cols: list[str]) -> pd.DataFrame:
    """Compute mean(|SHAP|) per feature across all HCPs, sorted descending."""
    mean_abs = shap_df[shap_feature_cols].abs().mean()
    total = mean_abs.sum()

    result = (
        mean_abs
        .rename("mean_abs_shap")
        .reset_index()
        .rename(columns={"index": "feature"})
        .sort_values("mean_abs_shap", ascending=False)
        .reset_index(drop=True)
    )
    result["pct_of_total"] = (result["mean_abs_shap"] / total * 100).round(1)

    print("\nGlobal SHAP importance (all HCPs):")
    print(f"  {'rank':<5}  {'feature':<45}  {'mean_abs_shap':>14}  {'pct_of_total':>12}")
    print(f"  {'----':<5}  {'-------':<45}  {'-------------':>14}  {'------------':>12}")
    for i, row in result.iterrows():
        print(f"  {i+1:<5}  {row['feature']:<45}  {row['mean_abs_shap']:>14.6f}  {row['pct_of_total']:>11.1f}%")

    return result


def top1pct_importance(
    shap_df: pd.DataFrame,
    shap_feature_cols: list[str],
) -> pd.DataFrame:
    """Compute mean(|SHAP|) for the top-1% most anomalous HCPs by IF score."""
    scores_df = pd.read_parquet(IF_SCORES_PATH, columns=["hcp_id", "anomaly_score"])
    scores_sorted = scores_df.sort_values("anomaly_score", ascending=False, kind="mergesort")
    n_top = int(np.ceil(len(scores_sorted) * 0.01))
    top_hcp_ids = set(scores_sorted.iloc[:n_top]["hcp_id"])

    print(f"\nTop-1% slice: {n_top} HCPs (of {len(scores_df)} total)")

    shap_top = shap_df[shap_df["hcp_id"].isin(top_hcp_ids)]
    assert len(shap_top) == n_top, (
        f"Expected {n_top} rows in top-1% SHAP slice, got {len(shap_top)}. "
        "Check for hcp_id mismatches between if_scores and shap_values."
    )

    mean_abs_top = shap_top[shap_feature_cols].abs().mean()
    result = (
        mean_abs_top
        .rename("mean_abs_shap_top1pct")
        .reset_index()
        .rename(columns={"index": "feature"})
        .sort_values("mean_abs_shap_top1pct", ascending=False)
        .reset_index(drop=True)
    )

    print("\nTop-10 features by SHAP importance in top-1% most anomalous HCPs:")
    print(f"  {'rank':<5}  {'feature':<45}  {'mean_abs_shap_top1pct':>22}")
    print(f"  {'----':<5}  {'-------':<45}  {'---------------------':>22}")
    for i, row in result.head(10).iterrows():
        print(f"  {i+1:<5}  {row['feature']:<45}  {row['mean_abs_shap_top1pct']:>22.6f}")

    return result


def main() -> None:
    feature_cols = load_feature_cols()
    shap_df, shap_feature_cols = load_shap(feature_cols)

    global_df = global_importance(shap_df, shap_feature_cols)
    top1pct_df = top1pct_importance(shap_df, shap_feature_cols)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    global_path  = OUTPUT_DIR / "shap_global_importance.parquet"
    top1pct_path = OUTPUT_DIR / "shap_top1pct_importance.parquet"

    global_df.to_parquet(global_path, index=False)
    top1pct_df.to_parquet(top1pct_path, index=False)

    print(f"\nSaved: {global_path}  ({len(global_df)} features)")
    print(f"Saved: {top1pct_path}  ({len(top1pct_df)} features)")


if __name__ == "__main__":
    main()
