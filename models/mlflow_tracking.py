"""
mlflow_tracking.py
Phase 2 — MLflow experiment tracking for compliance anomaly detection

Logs all Phase 2 outputs to MLflow for experiment tracking and model registry:
  - Isolation Forest model artifact + hyperparameters
  - Rule-based flag summary metrics (23 per-rule counts + overall rates)
  - Unified risk score distribution (tier counts, percentiles)
  - Feature importance proxy (Pearson correlation vs anomaly_score, top 20)
  - Ground truth recall/precision metrics (synthetic violation labels)

MLflow tracking URI:  http://localhost:5001
Experiment:           compliance_risk_phase2
Model registry name:  isolation_forest_hcp_risk

Ground truth note:
  ground_truth_labels.parquet contains synthetic violation labels from
  mart_hcp_risk_profile. Real Nova Pharma data will have different recall
  figures. Recall is the primary metric: missing a true violation is worse
  than a false positive in a compliance context.

Fallback:
  If MLflow is unreachable, all metrics are written to
  models/outputs/mlflow_fallback_metrics.json so no data is lost.

Usage:
    # Start MLflow server first
    mlflow server --port 5001

    # Then run tracking
    python3 models/mlflow_tracking.py

Prerequisites:
    python3 models/rule_based_flags.py   → rule_flags.parquet
    python3 models/isolation_forest.py   → if_scores.parquet
    python3 models/scorer.py             → risk_scores.parquet
    python3 features/feature_store.py    → feature_store.parquet, ground_truth_labels.parquet
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
MLFLOW_TRACKING_URI = "http://localhost:5001"
EXPERIMENT_NAME     = "compliance_risk_phase2"
MODEL_NAME          = "isolation_forest_hcp_risk"

# Input paths
IF_SCORES_PATH          = "models/outputs/if_scores.parquet"
RULE_FLAGS_PATH         = "models/outputs/rule_flags.parquet"
RISK_SCORES_PATH        = "models/outputs/risk_scores.parquet"
FEATURE_STORE_PATH      = "features/outputs/feature_store.parquet"
GROUND_TRUTH_PATH       = "features/outputs/ground_truth_labels.parquet"
IF_METADATA_PATH        = "models/outputs/if_metadata.json"
RULE_FLAGS_META_PATH    = "models/outputs/rule_flags_metadata.json"
RULES_PATH              = "compliance/rules.json"

# Output paths
FEATURE_IMPORTANCE_PATH = "models/outputs/feature_importance.csv"
FALLBACK_METRICS_PATH   = "models/outputs/mlflow_fallback_metrics.json"

# Scorer constants (must match scorer.py)
SCORER_RULE_WEIGHT   = 0.60
SCORER_IF_WEIGHT     = 0.40
SCORER_CRITICAL_PTS  = 40
SCORER_HIGH_PTS      = 20
SCORER_MEDIUM_PTS    = 10

# Feature importance: top N by |Pearson correlation| with anomaly_score
TOP_N_FEATURES = 20


# ─── MLflow setup ────────────────────────────────────────────────────────────

def setup_mlflow() -> tuple[mlflow.ActiveRun, bool]:
    """
    Configure MLflow tracking URI and start a run.

    Returns:
        (active_run, mlflow_reachable) — mlflow_reachable is False if the
        tracking server was unreachable. In that case run is a local run and
        the fallback JSON path is also written.

    Run name uses a UTC timestamp so each execution is uniquely identifiable
    in the experiment UI without needing to inspect run IDs.
    """
    run_name = f"phase2_scoring_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"

    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(EXPERIMENT_NAME)
        run = mlflow.start_run(run_name=run_name)
        logger.info(
            "MLflow run started: run_id={} | experiment={} | uri={}",
            run.info.run_id,
            EXPERIMENT_NAME,
            MLFLOW_TRACKING_URI,
        )
        return run, True

    except Exception as e:
        logger.error(
            "MLflow server unreachable at {} — {}\n"
            "Start with: mlflow server --port 5001\n"
            "Metrics will be saved to {} as fallback.",
            MLFLOW_TRACKING_URI,
            e,
            FALLBACK_METRICS_PATH,
        )
        # Fall back to a local file store so no data is lost
        mlflow.set_tracking_uri("file:./mlruns")
        mlflow.set_experiment(EXPERIMENT_NAME)
        run = mlflow.start_run(run_name=run_name)
        logger.warning("Using local file store fallback at ./mlruns")
        return run, False


# ─── Load all outputs ─────────────────────────────────────────────────────────

def load_all_outputs() -> dict:
    """
    Load all Phase 2 parquet and JSON outputs.

    Missing files are logged as warnings and skipped — downstream logging
    functions check for None before accessing each key.

    Returns dict with keys:
      if_scores, rule_flags, risk_scores, feature_store, ground_truth,
      if_metadata, rule_flags_metadata, rules
    """
    data: dict = {}

    def _load_parquet(key: str, path: str) -> None:
        p = Path(path)
        if not p.exists():
            logger.warning("Missing {} — skipping ({})", key, path)
            data[key] = None
            return
        data[key] = pd.read_parquet(p)
        logger.info("Loaded {}: {} rows", key, len(data[key]))

    def _load_json(key: str, path: str) -> None:
        p = Path(path)
        if not p.exists():
            logger.warning("Missing {} — skipping ({})", key, path)
            data[key] = {}
            return
        with open(p) as f:
            data[key] = json.load(f)
        logger.info("Loaded {}: {}", key, path)

    _load_parquet("if_scores",    IF_SCORES_PATH)
    _load_parquet("rule_flags",   RULE_FLAGS_PATH)
    _load_parquet("risk_scores",  RISK_SCORES_PATH)
    _load_parquet("feature_store", FEATURE_STORE_PATH)
    _load_parquet("ground_truth", GROUND_TRUTH_PATH)
    _load_json("if_metadata",         IF_METADATA_PATH)
    _load_json("rule_flags_metadata", RULE_FLAGS_META_PATH)
    _load_json("rules",               RULES_PATH)

    return data


# ─── Log parameters ───────────────────────────────────────────────────────────

def log_parameters(run: mlflow.ActiveRun, data: dict) -> None:
    """
    Log experiment parameters to MLflow.

    Isolation Forest params: read from if_metadata.json hyperparameters block.
    Rules params: read from rules.json metadata.
    Scorer params: constants matching scorer.py config.

    Parameters are experiment-level configuration (not per-run metrics). They
    are logged once per run and used to filter/compare runs in the MLflow UI.
    """
    if_meta = data.get("if_metadata", {})
    hp      = if_meta.get("hyperparameters", {})
    sd      = if_meta.get("score_distribution", {})
    rules   = data.get("rules", {})
    rules_meta = rules.get("metadata", {})

    # Count Nova Pharma as effective source (policy-specific overrides)
    np_override_count = sum(
        1 for r in rules.get("rules", [])
        if r.get("effective_source") == "Nova Pharma"
    )

    params = {
        # Isolation Forest
        "if_n_estimators":       hp.get("n_estimators", 200),
        "if_contamination":      hp.get("contamination", 0.10),
        "if_n_features":         hp.get("n_features", 104),
        "if_n_samples":          hp.get("n_samples", 97_011),
        "if_score_min":          sd.get("raw_score_min"),
        "if_score_max":          sd.get("raw_score_max"),
        "if_score_median":       sd.get("anomaly_score_median"),
        # Rules
        "rules_version":             rules_meta.get("version", "1.0"),
        "rules_total":               len(rules.get("rules", [])),
        "rules_applied":             23,
        "rules_nova_pharma_overrides": np_override_count,
        # Scorer
        "scorer_rule_weight":    SCORER_RULE_WEIGHT,
        "scorer_if_weight":      SCORER_IF_WEIGHT,
        "scorer_critical_pts":   SCORER_CRITICAL_PTS,
        "scorer_high_pts":       SCORER_HIGH_PTS,
        "scorer_medium_pts":     SCORER_MEDIUM_PTS,
    }

    for k, v in params.items():
        if v is not None:
            mlflow.log_param(k, v)

    logger.info("Logged {} parameters", len([v for v in params.values() if v is not None]))


# ─── Log Isolation Forest metrics ─────────────────────────────────────────────

def log_if_metrics(run: mlflow.ActiveRun, data: dict) -> None:
    """
    Log Isolation Forest anomaly score metrics.

    Percentiles are computed fresh from if_scores.parquet rather than
    read from metadata, giving exact population-level values.
    """
    if_scores = data.get("if_scores")
    if if_scores is None:
        logger.warning("if_scores not available — skipping IF metrics")
        return

    scores = if_scores["anomaly_score"].astype(float)
    outlier_rate = float(if_scores["if_is_outlier"].mean())

    metrics = {
        "if_outlier_rate": outlier_rate,
        "if_score_p25":    float(np.percentile(scores, 25)),
        "if_score_p50":    float(np.percentile(scores, 50)),
        "if_score_p75":    float(np.percentile(scores, 75)),
        "if_score_p95":    float(np.percentile(scores, 95)),
    }

    for k, v in metrics.items():
        mlflow.log_metric(k, v)

    logger.info(
        "IF metrics logged: outlier_rate={:.3f} | p50={:.1f} | p95={:.1f}",
        outlier_rate,
        metrics["if_score_p50"],
        metrics["if_score_p95"],
    )


# ─── Log rule flag metrics ─────────────────────────────────────────────────────

def log_flag_metrics(run: mlflow.ActiveRun, data: dict) -> None:
    """
    Log rule-based flag metrics.

    Logs one metric per rule (flag_<name>_count) so the MLflow UI can
    compare individual rule firing rates across runs. Also logs overall
    flag rate summary metrics.
    """
    rule_flags = data.get("rule_flags")
    if rule_flags is None:
        logger.warning("rule_flags not available — skipping flag metrics")
        return

    flag_cols = [c for c in rule_flags.columns if c.startswith("flag_") and rule_flags[c].dtype == bool]

    # Per-rule counts
    for col in flag_cols:
        mlflow.log_metric(f"flag_{col}_count", int(rule_flags[col].sum()))

    # Overall rates
    mlflow.log_metric("flag_any_rate",      float(rule_flags["has_any_flag"].mean()))
    mlflow.log_metric("flag_critical_rate", float(rule_flags["has_critical_flag"].mean()))
    mlflow.log_metric("flag_high_rate",     float((rule_flags["high_flags"] > 0).mean()))
    mlflow.log_metric("flag_medium_rate",   float((rule_flags["medium_flags"] > 0).mean()))

    logger.info(
        "Flag metrics logged: {} per-rule + 4 rate metrics | any_flag_rate={:.3f}",
        len(flag_cols),
        float(rule_flags["has_any_flag"].mean()),
    )


# ─── Log risk score metrics ────────────────────────────────────────────────────

def log_risk_score_metrics(run: mlflow.ActiveRun, data: dict) -> None:
    """
    Log unified risk score distribution and tier breakdown metrics.
    """
    risk_scores = data.get("risk_scores")
    if risk_scores is None:
        logger.warning("risk_scores not available — skipping risk score metrics")
        return

    scores = risk_scores["risk_score"].astype(float)
    tiers  = risk_scores["risk_tier"]
    n      = len(risk_scores)

    tier_n   = tiers.value_counts()
    tier_pct = tiers.value_counts(normalize=True)

    metrics = {
        "risk_score_median":        float(np.median(scores)),
        "risk_score_p90":           float(np.percentile(scores, 90)),
        "risk_score_p99":           float(np.percentile(scores, 99)),
        # Tier counts
        "risk_tier_critical_n":     int(tier_n.get("critical", 0)),
        "risk_tier_high_n":         int(tier_n.get("high", 0)),
        "risk_tier_medium_n":       int(tier_n.get("medium", 0)),
        "risk_tier_low_n":          int(tier_n.get("low", 0)),
        # Tier fractions
        "risk_tier_critical_pct":   float(tier_pct.get("critical", 0)),
        "risk_tier_high_pct":       float(tier_pct.get("high", 0)),
        "risk_tier_medium_pct":     float(tier_pct.get("medium", 0)),
        "risk_tier_low_pct":        float(tier_pct.get("low", 0)),
    }

    for k, v in metrics.items():
        mlflow.log_metric(k, v)

    logger.info(
        "Risk score metrics logged: median={:.1f} | critical={} ({:.1f}%) | high={} ({:.1f}%)",
        metrics["risk_score_median"],
        metrics["risk_tier_critical_n"],
        metrics["risk_tier_critical_pct"] * 100,
        metrics["risk_tier_high_n"],
        metrics["risk_tier_high_pct"] * 100,
    )


# ─── Ground truth recall/precision ────────────────────────────────────────────

def compute_ground_truth_metrics(data: dict) -> dict:
    """
    Compute and log ground truth recall and precision metrics.

    Joins risk_scores with ground_truth_labels on hcp_id.

    Metrics logged:
      gt_total_violations        — HCPs with has_violation == 1
      gt_violation_rate          — violation fraction of population
      gt_recall_any_flag         — violations caught by any rule flag
      gt_recall_critical         — violations in critical tier
      gt_recall_high_or_critical — violations in high or critical tier
      gt_precision_critical      — true violations in critical tier / all critical
      gt_if_outlier_recall       — violations flagged as IF outliers

    Why recall > precision in compliance:
      A missed violation (false negative) means uninvestigated fraud exposure.
      A false positive means an unnecessary audit. Recall is the primary metric.

    Returns dict of metric values (also logged to MLflow).
    """
    risk_scores  = data.get("risk_scores")
    ground_truth = data.get("ground_truth")

    if risk_scores is None or ground_truth is None:
        logger.warning("risk_scores or ground_truth not available — skipping GT metrics")
        return {}

    joined = risk_scores.merge(
        ground_truth[["hcp_id", "has_violation"]],
        on="hcp_id",
        how="inner",
    )

    if len(joined) == 0:
        logger.warning("GT join produced 0 rows — check hcp_id alignment")
        return {}

    violations   = joined[joined["has_violation"] == 1]
    n_violations = len(violations)
    n_total      = len(joined)

    if n_violations == 0:
        logger.warning("No GT violations found — skipping GT metrics")
        return {}

    n_critical   = int((joined["risk_tier"] == "critical").sum())

    gt_recall_any_flag = float(
        (violations["total_rule_flags"] > 0).sum() / n_violations
    )
    gt_recall_critical = float(
        (violations["risk_tier"] == "critical").sum() / n_violations
    )
    gt_recall_high_or_critical = float(
        violations["risk_tier"].isin(["critical", "high"]).sum() / n_violations
    )
    gt_precision_critical = float(
        joined[(joined["risk_tier"] == "critical") & (joined["has_violation"] == 1)].shape[0]
        / max(n_critical, 1)
    )
    gt_if_outlier_recall = float(
        (violations["if_is_outlier"] == 1).sum() / n_violations
    )

    metrics = {
        "gt_total_violations":        n_violations,
        "gt_violation_rate":          float(n_violations / n_total),
        "gt_recall_any_flag":         gt_recall_any_flag,
        "gt_recall_critical":         gt_recall_critical,
        "gt_recall_high_or_critical": gt_recall_high_or_critical,
        "gt_precision_critical":      gt_precision_critical,
        "gt_if_outlier_recall":       gt_if_outlier_recall,
    }

    for k, v in metrics.items():
        mlflow.log_metric(k, v)

    logger.info(
        "GT metrics: violations={} ({:.1f}%) | recall_any_flag={:.3f} "
        "| recall_high+critical={:.3f} | precision_critical={:.3f} "
        "| if_outlier_recall={:.3f}",
        n_violations,
        float(n_violations / n_total) * 100,
        gt_recall_any_flag,
        gt_recall_high_or_critical,
        gt_precision_critical,
        gt_if_outlier_recall,
    )
    return metrics


# ─── Feature importance proxy ─────────────────────────────────────────────────

def compute_feature_importance(data: dict) -> pd.DataFrame | None:
    """
    Compute feature importance as |Pearson correlation| between each feature
    and IF anomaly_score.

    Why Pearson correlation as proxy:
      True Shapley values require per-sample tree traversal across all 200 IF
      trees — expensive for 97,011 × 104 features. Pearson correlation provides
      a fast directional proxy: high |r| means the feature strongly co-varies
      with the anomaly score, making it a plausible driver of anomalous HCP
      identification. Phase 3 will replace this with SHAP values.

    Handles degenerate columns (zero variance — constant feature) by assigning
    correlation = 0.0 rather than NaN, which would cause sort failures.

    Returns DataFrame with columns [feature, mean_abs_score_diff], sorted
    descending by importance. Also saves to feature_importance.csv for
    artifact logging. Returns None if inputs unavailable.
    """
    feature_store = data.get("feature_store")
    if_scores     = data.get("if_scores")

    if feature_store is None or if_scores is None:
        logger.warning("feature_store or if_scores not available — skipping feature importance")
        return None

    # Align on index (both have same 97,011 rows in same order from the same pipeline)
    if len(feature_store) != len(if_scores):
        logger.warning(
            "feature_store rows ({}) != if_scores rows ({}) — skipping feature importance",
            len(feature_store),
            len(if_scores),
        )
        return None

    anomaly_scores = if_scores["anomaly_score"].astype(float).values
    feature_cols   = feature_store.select_dtypes(include=[np.number]).columns.tolist()

    correlations: list[dict] = []
    for col in feature_cols:
        vals = feature_store[col].astype(float).values
        if np.std(vals) < 1e-9:
            # Constant feature — zero information
            corr = 0.0
        else:
            corr_matrix = np.corrcoef(vals, anomaly_scores)
            corr = float(np.abs(corr_matrix[0, 1]))
            if np.isnan(corr):
                corr = 0.0
        correlations.append({"feature": col, "mean_abs_score_diff": corr})

    importance_df = (
        pd.DataFrame(correlations)
        .sort_values("mean_abs_score_diff", ascending=False)
        .head(TOP_N_FEATURES)
        .reset_index(drop=True)
    )

    out_path = Path(FEATURE_IMPORTANCE_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    importance_df.to_csv(out_path, index=False)
    logger.info("Feature importance saved: {} (top {})", out_path, TOP_N_FEATURES)

    top5 = importance_df.head(5)["feature"].tolist()
    logger.info("Top 5 features by |Pearson correlation|: {}", top5)

    return importance_df


# ─── Log artifacts ────────────────────────────────────────────────────────────

def log_artifacts(run: mlflow.ActiveRun) -> None:
    """
    Log file artifacts to MLflow.

    Artifacts are the actual output files (parquets, JSONs, CSVs) — not
    metrics. They can be downloaded from the MLflow UI for offline inspection.

    Skips missing files with a warning rather than raising.
    """
    artifacts = [
        IF_SCORES_PATH,
        IF_METADATA_PATH,
        RULE_FLAGS_META_PATH,
        RISK_SCORES_PATH,
        "models/outputs/risk_scores_metadata.json",
        RULES_PATH,
        FEATURE_IMPORTANCE_PATH,
    ]

    logged = 0
    for path_str in artifacts:
        path = Path(path_str)
        if not path.exists():
            logger.warning(f"Artifact not found, skipping: {path}")
            continue
        try:
            mlflow.log_artifact(str(path))
            logged += 1
            logger.debug(f"Logged artifact: {path}")
        except Exception as e:
            logger.warning(f"Failed to log artifact {path}: {e}")

    logger.info("Logged {} artifacts", logged)


# ─── Fallback JSON ────────────────────────────────────────────────────────────

def _save_fallback_metrics(
    gt_metrics: dict,
    importance_df: pd.DataFrame | None,
) -> None:
    """
    Save all computed metrics to a local JSON file as a fallback when
    MLflow server is unreachable and a local run was used instead.

    This ensures no data is lost even if the tracking server is down.
    """
    fallback = {
        "generated_at":          datetime.now(timezone.utc).isoformat(),
        "mlflow_tracking_uri":   MLFLOW_TRACKING_URI,
        "experiment":            EXPERIMENT_NAME,
        "ground_truth_metrics":  gt_metrics,
        "top_features":          (
            importance_df[["feature", "mean_abs_score_diff"]].to_dict(orient="records")
            if importance_df is not None
            else []
        ),
    }
    path = Path(FALLBACK_METRICS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(fallback, f, indent=2)
    logger.info("Fallback metrics saved to {}", path)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info("mlflow_tracking.py — Phase 2 experiment tracking")
    logger.info("=" * 60)

    # 1. Set up MLflow and start run
    run, mlflow_reachable = setup_mlflow()

    with run:
        # 2. Load all outputs
        data = load_all_outputs()

        # 3. Log parameters
        log_parameters(run, data)

        # 4. Log IF metrics
        log_if_metrics(run, data)

        # 5. Log flag metrics
        log_flag_metrics(run, data)

        # 6. Log risk score metrics
        log_risk_score_metrics(run, data)

        # 7. Compute and log ground truth metrics
        gt_metrics = compute_ground_truth_metrics(data)

        # 8. Compute and save feature importance (also logs to MLflow via artifact)
        importance_df = compute_feature_importance(data)

        # 9. Log all file artifacts
        log_artifacts(run)

        # 10. Save fallback JSON (useful even when MLflow is reachable)
        _save_fallback_metrics(gt_metrics, importance_df)

        elapsed = time.time() - start

        # ── Summary ──────────────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("MLflow tracking complete in {:.2f}s", elapsed)
        logger.info("Run ID:           {}", run.info.run_id)
        logger.info("Experiment:       {}", EXPERIMENT_NAME)
        logger.info("Tracking URI:     {}", MLFLOW_TRACKING_URI)

        if gt_metrics:
            logger.info(
                "GT recall (any flag):       {:.1%}",
                gt_metrics.get("gt_recall_any_flag", 0),
            )
            logger.info(
                "GT recall (high+critical):  {:.1%}",
                gt_metrics.get("gt_recall_high_or_critical", 0),
            )
            logger.info(
                "GT precision (critical):    {:.1%}",
                gt_metrics.get("gt_precision_critical", 0),
            )

        if importance_df is not None:
            top5 = importance_df.head(5)["feature"].tolist()
            logger.info("Top 5 features: {}", top5)

        if mlflow_reachable:
            logger.info("View at: {}", MLFLOW_TRACKING_URI)
        else:
            logger.warning(
                "MLflow server was unreachable — metrics saved to {}",
                FALLBACK_METRICS_PATH,
            )
            logger.warning("Start server with: mlflow server --port 5001")
        logger.info("=" * 60)

        print("\n" + "=" * 60)
        print("Phase 2 MLflow Tracking Summary")
        print("=" * 60)
        print(f"  MLflow run ID:              {run.info.run_id}")
        print(f"  Experiment:                 {EXPERIMENT_NAME}")
        print(f"  Tracking URI:               {MLFLOW_TRACKING_URI}")
        if gt_metrics:
            print(f"  GT recall (any flag):       {gt_metrics.get('gt_recall_any_flag', 0):.1%}")
            print(f"  GT recall (high+critical):  {gt_metrics.get('gt_recall_high_or_critical', 0):.1%}")
            print(f"  GT precision (critical):    {gt_metrics.get('gt_precision_critical', 0):.1%}")
        if importance_df is not None:
            print(f"  Top 5 features (|r|):")
            for _, row in importance_df.head(5).iterrows():
                print(f"    {row['feature']:<45} {row['mean_abs_score_diff']:.4f}")
        if mlflow_reachable:
            print(f"\n  View at: {MLFLOW_TRACKING_URI}")
        else:
            print(f"\n  MLflow unreachable — fallback: {FALLBACK_METRICS_PATH}")
            print("  Start server with: mlflow server --port 5001")
        print("=" * 60)


if __name__ == "__main__":
    main()
