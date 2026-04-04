"""
scorer.py
Phase 2 — Unified compliance risk scorer

Combines rule-based flags (rule_based_flags.py, Task 2.8) and Isolation Forest
anomaly scores (isolation_forest.py, Task 2.9) into a single 0–100 risk score
per HCP.

Inputs:
  models/outputs/rule_flags.parquet   — 23 boolean flags + severity summary per HCP
  models/outputs/if_scores.parquet    — anomaly_score [0, 100] per HCP

Output:
  models/outputs/risk_scores.parquet  — unified risk_score + risk_tier per HCP
  models/outputs/risk_scores_metadata.json

Scoring formula:
  rule_score  = severity-weighted sum of fired flags, capped at 100
                  critical flag  → +40 pts each
                  high flag      → +20 pts each
                  medium flag    → +10 pts each
  risk_score  = RULE_WEIGHT × rule_score + IF_WEIGHT × anomaly_score
                  default: 60% rule-based, 40% IF

  Risk tiers (score-based, with critical-flag floor):
    critical  → risk_score >= 60
    high      → risk_score >= 25 (or critical flag present and tier would be 'low'/'medium')
    medium    → risk_score >= 10
    low       → risk_score < 10

  Critical-flag floor rule:
    Any HCP with a critical-severity flag fires (cap breach, chronic FMV) will
    always be assigned at least 'high' tier regardless of composite score.
    This prevents a low anomaly_score from masking a known critical violation.

Why 60/40 rule/IF split:
  Rule-based flags are deterministic and auditable — a critical flag means the
  HCP definitively exceeded a regulatory threshold. IF scores are continuous but
  less interpretable — they signal statistical deviation, not proven violation.
  Weighting rules higher ensures known violations dominate the score while IF
  still surfaces unknown patterns for investigation.

Consumers:
  - Phase 3 Policy Agent: reads `flagged_rule_ids` to cite policy sections
  - EDA notebook (Task 2.13): risk score distribution analysis
  - API / UI layer: serves risk_score + risk_tier per HCP

Usage:
    python3 models/scorer.py

Prerequisites:
    python3 models/rule_based_flags.py   (produces rule_flags.parquet)
    python3 models/isolation_forest.py   (produces if_scores.parquet)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

# ─── Config ──────────────────────────────────────────────────────────────────
RULE_FLAGS_PATH = "models/outputs/rule_flags.parquet"
IF_SCORES_PATH  = "models/outputs/if_scores.parquet"
OUTPUT_DIR      = "models/outputs"

# Composite score weights — must sum to 1.0
RULE_WEIGHT = 0.60   # rule-based component weight
IF_WEIGHT   = 0.40   # Isolation Forest component weight

# Severity → point contribution to rule_score (sum capped at 100)
SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 40,
    "high":     20,
    "medium":   10,
}

# Risk tier thresholds (applied to composite risk_score [0, 100])
TIER_CRITICAL = 60.0
TIER_HIGH     = 25.0
TIER_MEDIUM   = 10.0

# Validation bounds
EXPECTED_ROWS      = 97_011
CRITICAL_TIER_MAX  = 0.10   # at most 10% of HCPs in critical tier
HIGH_PLUS_TIER_MAX = 0.60   # at most 35% in high+critical combined

# Per-flag severity mapping (mirrors rule_based_flags.py RULE_SEVERITY)
# Duplicated here so scorer.py is self-contained — no import from rule_based_flags.
FLAG_SEVERITY: dict[str, str] = {
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

ALL_FLAGS = list(FLAG_SEVERITY.keys())

VALID_TIERS = {"critical", "high", "medium", "low"}


# ─── Load inputs ─────────────────────────────────────────────────────────────

def load_rule_flags() -> pd.DataFrame:
    """
    Load models/outputs/rule_flags.parquet produced by rule_based_flags.py.

    Expected columns:
      hcp_id, flag_*, total_rule_flags, critical_flags, high_flags,
      medium_flags, has_any_flag, has_critical_flag, most_severe_flag,
      flagged_rule_ids

    Raises FileNotFoundError if not present.
    Run models/rule_based_flags.py first.
    """
    path = Path(RULE_FLAGS_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"Rule flags not found: {path}\n"
            "Run models/rule_based_flags.py first."
        )
    df = pd.read_parquet(path)
    logger.info(
        "Rule flags loaded: {} rows × {} columns",
        len(df),
        len(df.columns),
    )
    return df


def load_if_scores() -> pd.DataFrame:
    """
    Load models/outputs/if_scores.parquet produced by isolation_forest.py.

    Expected columns:
      hcp_id, anomaly_score [0, 100], if_raw_score, if_is_outlier,
      anomaly_percentile

    Raises FileNotFoundError if not present.
    Run models/isolation_forest.py first.
    """
    path = Path(IF_SCORES_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"IF scores not found: {path}\n"
            "Run models/isolation_forest.py first."
        )
    df = pd.read_parquet(path)
    logger.info(
        "IF scores loaded: {} rows × {} columns",
        len(df),
        len(df.columns),
    )
    return df


# ─── Merge inputs ─────────────────────────────────────────────────────────────

def merge_inputs(flags_df: pd.DataFrame, if_df: pd.DataFrame) -> pd.DataFrame:
    """
    Inner-join rule flags and IF scores on hcp_id.

    Both inputs should have 97,011 rows with the same hcp_id universe.
    An inner join is used to guarantee no null anomaly_score or rule_score
    values in the merged result.

    Raises ValueError if the merged row count does not equal EXPECTED_ROWS.
    """
    merged = flags_df.merge(
        if_df[["hcp_id", "anomaly_score", "if_is_outlier", "anomaly_percentile"]],
        on="hcp_id",
        how="inner",
        validate="1:1",
    )

    if len(merged) != EXPECTED_ROWS:
        raise ValueError(
            f"Merged DataFrame has {len(merged)} rows — expected {EXPECTED_ROWS}. "
            "hcp_id mismatch between rule_flags.parquet and if_scores.parquet. "
            "Regenerate both from the same feature_store.parquet run."
        )

    logger.info("Merged inputs: {} rows (inner join on hcp_id)", len(merged))
    return merged


# ─── Rule score ───────────────────────────────────────────────────────────────

def compute_rule_score(merged_df: pd.DataFrame) -> pd.Series:
    """
    Compute a severity-weighted rule score [0, 100] per HCP.

    For each HCP, sum the severity weights of all fired flags:
      critical flag fired → +40 pts
      high flag fired     → +20 pts
      medium flag fired   → +10 pts

    Cap at 100. An HCP firing all 23 flags has a theoretical max of:
      4 critical × 40 = 160
      10 high     × 20 = 200
      9 medium    × 10 = 90
    Total = 450 → capped to 100.

    Returns a float32 Series aligned with merged_df.index.
    """
    flag_cols = [c for c in ALL_FLAGS if c in merged_df.columns]

    # Build weight vector for present flag columns
    weights = np.array([
        SEVERITY_WEIGHTS.get(FLAG_SEVERITY.get(c, "medium"), 10)
        for c in flag_cols
    ], dtype=np.float32)

    # Flag matrix as float (bool → 0.0 / 1.0)
    flag_matrix = merged_df[flag_cols].astype(np.float32).values  # (n, n_flags)

    # Weighted sum per HCP
    raw_rule_score = flag_matrix @ weights  # (n,)

    # Cap at 100
    rule_score = np.minimum(raw_rule_score, 100.0)

    logger.info(
        "Rule score: min={:.1f}, p25={:.1f}, median={:.1f}, "
        "p75={:.1f}, max={:.1f}",
        float(np.min(rule_score)),
        float(np.percentile(rule_score, 25)),
        float(np.median(rule_score)),
        float(np.percentile(rule_score, 75)),
        float(np.max(rule_score)),
    )
    return pd.Series(rule_score, index=merged_df.index, dtype=np.float32)


# ─── Composite risk score ─────────────────────────────────────────────────────

def compute_risk_score(
    rule_score: pd.Series,
    anomaly_score: pd.Series,
) -> pd.Series:
    """
    Compute the composite risk score as a weighted average.

    risk_score = RULE_WEIGHT × rule_score + IF_WEIGHT × anomaly_score
               = 0.60 × rule_score + 0.40 × anomaly_score

    Both inputs are [0, 100]; the output is also [0, 100].

    Returns float32 Series.
    """
    risk_score = (
        RULE_WEIGHT * rule_score.astype(np.float32)
        + IF_WEIGHT  * anomaly_score.astype(np.float32)
    ).clip(0.0, 100.0)

    logger.info(
        "Risk score: min={:.1f}, p25={:.1f}, median={:.1f}, "
        "p75={:.1f}, p95={:.1f}, max={:.1f}",
        float(risk_score.min()),
        float(risk_score.quantile(0.25)),
        float(risk_score.median()),
        float(risk_score.quantile(0.75)),
        float(risk_score.quantile(0.95)),
        float(risk_score.max()),
    )
    return risk_score.astype(np.float32)


# ─── Risk tier ────────────────────────────────────────────────────────────────

def assign_risk_tier(
    risk_score: pd.Series,
    most_severe_flag: pd.Series,
) -> pd.Series:
    """
    Assign a risk tier to each HCP based on composite score + flag severity floor.

    Score-based thresholds:
      critical  → risk_score >= 75
      high      → risk_score >= 50
      medium    → risk_score >= 25
      low       → risk_score < 25

    Critical-flag floor:
      If an HCP has a critical-severity flag (annual cap breach or chronic FMV
      breach), their tier is floored at 'high' regardless of score. This
      prevents a low anomaly_score (HCP is otherwise statistically normal)
      from masking a confirmed regulatory threshold breach.

      A critical flag alone cannot elevate to 'critical' tier — that requires
      a score >= 75, ensuring the score reflects the full risk picture.

    Returns a categorical Series with values in {critical, high, medium, low}.
    """
    # Score-based tier
    tier = pd.cut(
        risk_score,
        bins=[-0.001, TIER_MEDIUM, TIER_HIGH, TIER_CRITICAL, 100.001],
        labels=["low", "medium", "high", "critical"],
        right=False,
    ).astype(str)

    # Critical-flag floor: ensure at least 'high' if a critical flag fired
    has_critical_flag = most_severe_flag == "critical"
    tier_is_low_or_medium = tier.isin(["low", "medium"])
    tier = np.where(
        has_critical_flag & tier_is_low_or_medium,
        "high",
        tier,
    )

    tier_series = pd.Series(tier, index=risk_score.index, dtype=str)

    dist = tier_series.value_counts().to_dict()
    logger.info(
        "Risk tiers: critical={} | high={} | medium={} | low={}",
        dist.get("critical", 0),
        dist.get("high", 0),
        dist.get("medium", 0),
        dist.get("low", 0),
    )
    return tier_series


# ─── Build output DataFrame ───────────────────────────────────────────────────

def build_scores_df(merged_df: pd.DataFrame, rule_score: pd.Series, risk_score: pd.Series, risk_tier: pd.Series) -> pd.DataFrame:
    """
    Assemble the final output DataFrame.

    Output schema (one row per HCP, 97,011 rows):

      hcp_id              — HCP identifier
      risk_score          — unified [0, 100] composite risk score
      risk_tier           — 'critical' | 'high' | 'medium' | 'low'
      rule_score          — severity-weighted rule component [0, 100]
      anomaly_score       — IF anomaly component [0, 100] (pass-through)
      total_rule_flags    — count of all fired flags
      critical_flags      — count of critical-severity flags fired
      high_flags          — count of high-severity flags fired
      medium_flags        — count of medium-severity flags fired
      most_severe_flag    — 'critical' | 'high' | 'medium' | 'none'
      flagged_rule_ids    — comma-separated policy rule IDs (for Phase 3 Agent)
      if_is_outlier       — 1 if IF labeled as outlier (top ~10%), else 0
      anomaly_percentile  — IF score percentile rank [0.0, 1.0]
    """
    passthrough_cols = [
        "hcp_id",
        "total_rule_flags",
        "critical_flags",
        "high_flags",
        "medium_flags",
        "most_severe_flag",
        "flagged_rule_ids",
    ]
    out = merged_df[passthrough_cols].copy()

    out["risk_score"]         = risk_score.values
    out["risk_tier"]          = risk_tier.values
    out["rule_score"]         = rule_score.values
    out["anomaly_score"]      = merged_df["anomaly_score"].astype(np.float32).values
    out["if_is_outlier"]      = merged_df["if_is_outlier"].values
    out["anomaly_percentile"] = merged_df["anomaly_percentile"].astype(np.float32).values

    # Reorder: identity → scores → tiers → flag summary → citations → IF
    col_order = [
        "hcp_id",
        "risk_score",
        "risk_tier",
        "rule_score",
        "anomaly_score",
        "total_rule_flags",
        "critical_flags",
        "high_flags",
        "medium_flags",
        "most_severe_flag",
        "flagged_rule_ids",
        "if_is_outlier",
        "anomaly_percentile",
    ]
    return out[[c for c in col_order if c in out.columns]]


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_scores(scores_df: pd.DataFrame) -> bool:
    """
    Validate the unified risk scores before saving.

    Checks:
      1. Row count == 97,011
      2. No nulls in risk_score
      3. risk_score in [0.0, 100.0]
      4. risk_tier values in {critical, high, medium, low}
      5. critical tier rate < 10% (sanity bound — critical should be rare)
      6. high+critical tier rate < 35%
      7. hcp_id unique — no duplicate HCPs

    Logs PASS/FAIL per check. Raises ValueError if any check fails.
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
        len(scores_df) == EXPECTED_ROWS,
        f"{len(scores_df)} (expected {EXPECTED_ROWS})",
    )

    null_count = int(scores_df["risk_score"].isnull().sum())
    _check("No nulls in risk_score", null_count == 0, f"{null_count} nulls")

    score_min = float(scores_df["risk_score"].min())
    score_max = float(scores_df["risk_score"].max())
    _check(
        "risk_score in [0.0, 100.0]",
        score_min >= 0.0 and score_max <= 100.0,
        f"range [{score_min:.2f}, {score_max:.2f}]",
    )

    invalid_tiers = set(scores_df["risk_tier"].unique()) - VALID_TIERS
    _check(
        "risk_tier values valid",
        len(invalid_tiers) == 0,
        f"Invalid: {invalid_tiers}" if invalid_tiers else "",
    )

    critical_rate = float((scores_df["risk_tier"] == "critical").mean())
    _check(
        f"critical tier rate < {CRITICAL_TIER_MAX:.0%}",
        critical_rate < CRITICAL_TIER_MAX,
        f"{critical_rate:.3f} ({critical_rate * 100:.1f}%)",
    )

    high_plus_rate = float(scores_df["risk_tier"].isin(["high", "critical"]).mean())
    _check(
        f"high+critical tier rate < {HIGH_PLUS_TIER_MAX:.0%}",
        high_plus_rate < HIGH_PLUS_TIER_MAX,
        f"{high_plus_rate:.3f} ({high_plus_rate * 100:.1f}%)",
    )

    n_unique = int(scores_df["hcp_id"].nunique())
    _check(
        "hcp_id unique",
        n_unique == len(scores_df),
        f"{n_unique} unique of {len(scores_df)}",
    )

    logger.info(
        "Validation: {}/{} checks passed",
        checks_passed,
        checks_passed + checks_failed,
    )
    if checks_failed > 0:
        raise ValueError(
            f"Risk score validation failed: {checks_failed} check(s) — see logs above"
        )
    return True


# ─── Save outputs ─────────────────────────────────────────────────────────────

def save_outputs(
    scores_df: pd.DataFrame,
    elapsed_s: float,
) -> dict:
    """
    Save unified risk scores and metadata.

    models/outputs/risk_scores.parquet:
        One row per HCP. All score + tier + flag summary columns.
        Primary input for Phase 3 Policy Agent and API layer.

    models/outputs/risk_scores_metadata.json:
        Score distribution, tier distribution, weighting params, timing.

    Returns dict of output paths.
    """
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    scores_path = out_dir / "risk_scores.parquet"
    meta_path   = out_dir / "risk_scores_metadata.json"

    scores_df.to_parquet(scores_path, index=False)
    logger.info("Saved risk scores: {} ({} rows)", scores_path, len(scores_df))

    tier_counts = scores_df["risk_tier"].value_counts().to_dict()

    metadata = {
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "total_hcps":       len(scores_df),
        "scoring_weights": {
            "rule_weight": RULE_WEIGHT,
            "if_weight":   IF_WEIGHT,
        },
        "severity_weights":   SEVERITY_WEIGHTS,
        "tier_thresholds": {
            "critical": TIER_CRITICAL,
            "high":     TIER_HIGH,
            "medium":   TIER_MEDIUM,
        },
        "tier_distribution": {
            "critical": int(tier_counts.get("critical", 0)),
            "high":     int(tier_counts.get("high", 0)),
            "medium":   int(tier_counts.get("medium", 0)),
            "low":      int(tier_counts.get("low", 0)),
        },
        "tier_rates": {
            "critical": float((scores_df["risk_tier"] == "critical").mean()),
            "high":     float((scores_df["risk_tier"] == "high").mean()),
            "medium":   float((scores_df["risk_tier"] == "medium").mean()),
            "low":      float((scores_df["risk_tier"] == "low").mean()),
        },
        "risk_score_distribution": {
            "mean":   float(scores_df["risk_score"].mean()),
            "median": float(scores_df["risk_score"].median()),
            "std":    float(scores_df["risk_score"].std()),
            "p25":    float(scores_df["risk_score"].quantile(0.25)),
            "p75":    float(scores_df["risk_score"].quantile(0.75)),
            "p90":    float(scores_df["risk_score"].quantile(0.90)),
            "p95":    float(scores_df["risk_score"].quantile(0.95)),
            "p99":    float(scores_df["risk_score"].quantile(0.99)),
            "min":    float(scores_df["risk_score"].min()),
            "max":    float(scores_df["risk_score"].max()),
        },
        "rule_score_distribution": {
            "mean":   float(scores_df["rule_score"].mean()),
            "median": float(scores_df["rule_score"].median()),
            "p95":    float(scores_df["rule_score"].quantile(0.95)),
            "max":    float(scores_df["rule_score"].max()),
        },
        "elapsed_s":  round(elapsed_s, 2),
        "output_columns": scores_df.columns.tolist(),
    }

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved metadata: {}", meta_path)

    return {
        "risk_scores": str(scores_path),
        "metadata":    str(meta_path),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info("scorer.py — Phase 2 unified risk scoring")
    logger.info("=" * 60)
    logger.info(
        "Weights: rule={:.0%}, IF={:.0%} | Severity: critical=+{}, high=+{}, medium=+{}",
        RULE_WEIGHT,
        IF_WEIGHT,
        SEVERITY_WEIGHTS["critical"],
        SEVERITY_WEIGHTS["high"],
        SEVERITY_WEIGHTS["medium"],
    )

    # 1. Load inputs
    flags_df = load_rule_flags()
    if_df    = load_if_scores()

    # 2. Merge on hcp_id (inner join — validates universe alignment)
    merged_df = merge_inputs(flags_df, if_df)

    # 3. Compute rule score (severity-weighted, capped at 100)
    rule_score = compute_rule_score(merged_df)

    # 4. Compute composite risk score
    risk_score = compute_risk_score(rule_score, merged_df["anomaly_score"])

    # 5. Assign risk tier (score-based with critical-flag floor)
    risk_tier = assign_risk_tier(risk_score, merged_df["most_severe_flag"])

    # 6. Assemble output DataFrame
    scores_df = build_scores_df(merged_df, rule_score, risk_score, risk_tier)

    # 7. Validate
    validate_scores(scores_df)

    elapsed = time.time() - start

    # 8. Save
    output_paths = save_outputs(scores_df, elapsed)

    # ── Summary ──────────────────────────────────────────────────────────────
    tier_counts = scores_df["risk_tier"].value_counts()
    logger.info("=" * 60)
    logger.info("Scoring complete in {:.2f}s", elapsed)
    logger.info(
        "Risk tier distribution: critical={} ({:.1f}%) | high={} ({:.1f}%) "
        "| medium={} ({:.1f}%) | low={} ({:.1f}%)",
        int(tier_counts.get("critical", 0)),
        float((scores_df["risk_tier"] == "critical").mean()) * 100,
        int(tier_counts.get("high", 0)),
        float((scores_df["risk_tier"] == "high").mean()) * 100,
        int(tier_counts.get("medium", 0)),
        float((scores_df["risk_tier"] == "medium").mean()) * 100,
        int(tier_counts.get("low", 0)),
        float((scores_df["risk_tier"] == "low").mean()) * 100,
    )
    logger.info(
        "risk_score: median={:.1f} | p90={:.1f} | p99={:.1f}",
        float(scores_df["risk_score"].median()),
        float(scores_df["risk_score"].quantile(0.90)),
        float(scores_df["risk_score"].quantile(0.99)),
    )
    logger.info("Outputs: {}", output_paths)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
