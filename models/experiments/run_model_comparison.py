"""
Compare IsolationForest, COPOD, and rule scores against three label axes.

Primary evaluation target:
  hcp_violation_profile — latent ground truth injected at HCP generation time
  (independent of rule engine; see ground_truth_provenance.md)

Calibration checks:
  has_violation / is_high — derived from record-level violation detection
  (correlated with rule engine by design; see ground_truth_provenance.md)

Inputs:
  models/outputs/if_scores.parquet                      — anomaly_score per HCP
  models/outputs/copod_scores.parquet                   — copod_score per HCP
  models/outputs/risk_scores.parquet                    — rule_score per HCP
  features/outputs/ground_truth_labels.parquet          — has_violation, ground_truth_max_severity
  models/experiments/outputs/hcp_profile_labels.parquet — hcp_violation_profile

Output:
  models/experiments/outputs/model_comparison_full.parquet
"""

from pathlib import Path

import pandas as pd

from evaluate_score import evaluate_score, to_comparison_rows

IF_SCORES_PATH    = "models/outputs/if_scores.parquet"
COPOD_SCORES_PATH = "models/outputs/copod_scores.parquet"
RISK_SCORES_PATH  = "models/outputs/risk_scores.parquet"
LABELS_PATH       = "features/outputs/ground_truth_labels.parquet"
PROFILE_PATH      = "models/experiments/outputs/hcp_profile_labels.parquet"
OUTPUT_PATH       = "models/experiments/outputs/model_comparison_full.parquet"
OLD_OUTPUT_PATH   = "models/experiments/outputs/model_comparison_if_vs_rules.parquet"

COMBINATIONS = [
    # ── Primary: latent violation profile (independent ground truth) ──────────
    ("anomaly_score", "is_serious"),
    ("copod_score",   "is_serious"),
    ("rule_score",    "is_serious"),
    ("anomaly_score", "is_moderate_or_worse"),
    ("copod_score",   "is_moderate_or_worse"),
    ("rule_score",    "is_moderate_or_worse"),
    # ── Calibration: record-level violation detection (correlated with rules) ─
    ("anomaly_score", "is_high"),
    ("copod_score",   "is_high"),
    ("rule_score",    "is_high"),
    ("anomaly_score", "has_violation"),
    ("copod_score",   "has_violation"),
    ("rule_score",    "has_violation"),
]


def main() -> None:
    labels        = pd.read_parquet(LABELS_PATH)[["hcp_id", "has_violation", "ground_truth_max_severity"]]
    if_scores     = pd.read_parquet(IF_SCORES_PATH)[["hcp_id", "anomaly_score"]]
    copod_scores  = pd.read_parquet(COPOD_SCORES_PATH)[["hcp_id", "copod_score"]]
    rule_scores   = pd.read_parquet(RISK_SCORES_PATH)[["hcp_id", "rule_score"]]
    profiles      = pd.read_parquet(PROFILE_PATH)[["hcp_id", "hcp_violation_profile"]]

    merged = (
        labels
        .merge(if_scores,    on="hcp_id", how="left")
        .merge(copod_scores, on="hcp_id", how="left")
        .merge(rule_scores,  on="hcp_id", how="left")
        .merge(profiles,     on="hcp_id", how="left")
    )

    merged["is_high"]              = (merged["ground_truth_max_severity"] == "high").astype(int)
    merged["rule_score"]           = merged["rule_score"].fillna(0)
    merged["is_serious"]           = (merged["hcp_violation_profile"] == "serious").astype(int)
    merged["is_moderate_or_worse"] = merged["hcp_violation_profile"].isin(["serious", "moderate"]).astype(int)

    n_null_anomaly = merged["anomaly_score"].isnull().sum()
    assert n_null_anomaly == 0, (
        f"anomaly_score has {n_null_anomaly} nulls — IF coverage is incomplete. "
        "Regenerate models/outputs/if_scores.parquet."
    )

    n_null_copod = merged["copod_score"].isnull().sum()
    assert n_null_copod == 0, (
        f"copod_score has {n_null_copod} nulls — COPOD coverage is incomplete. "
        "Regenerate models/outputs/copod_scores.parquet."
    )

    n_null_profile = merged["hcp_violation_profile"].isnull().sum()
    assert n_null_profile == 0, (
        f"hcp_violation_profile has {n_null_profile} nulls — profile coverage is incomplete. "
        "Regenerate models/experiments/outputs/hcp_profile_labels.parquet."
    )

    all_rows = []
    for score_col, label_col in COMBINATIONS:
        result = evaluate_score(score_col, label_col, merged)
        all_rows.extend(to_comparison_rows(result))

    comparison_df = pd.DataFrame(all_rows)

    assert len(comparison_df) == 40, (
        f"Expected 40 rows (36 top-K + 4 natural-threshold), got {len(comparison_df)}"
    )

    out_path = Path(OUTPUT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    comparison_df.to_parquet(out_path, index=False)
    print(f"Saved: {out_path}  ({len(comparison_df)} rows)\n")

    old_path = Path(OLD_OUTPUT_PATH)
    if old_path.exists():
        old_path.unlink()
        print(f"Deleted: {old_path}\n")

    display = comparison_df.copy()
    display["precision"] = (display["precision"] * 100).round(1).astype(str) + "%"
    display["recall"]    = (display["recall"]    * 100).round(1).astype(str) + "%"
    display["f1"]        = display["f1"].round(2)
    display["lift"]      = display["lift"].round(2)
    display = display.drop(columns=["base_rate"])

    label_order = ["is_serious", "is_moderate_or_worse", "is_high", "has_violation"]
    display["_label_rank"] = display["label"].map({l: i for i, l in enumerate(label_order)})
    display = display.sort_values(["_label_rank", "model", "threshold"]).drop(columns=["_label_rank"])

    col_widths = {col: max(len(col), display[col].astype(str).str.len().max()) for col in display.columns}
    header = "  ".join(col.ljust(col_widths[col]) for col in display.columns)
    sep    = "  ".join("-" * col_widths[col]       for col in display.columns)
    print(header)
    print(sep)
    for _, row in display.iterrows():
        print("  ".join(str(row[col]).ljust(col_widths[col]) for col in display.columns))


if __name__ == "__main__":
    main()
