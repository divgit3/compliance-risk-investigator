"""
agents/tools/data_tools.py — LangChain tools for querying Phase 2 parquet outputs
and compliance/rules.json.

Tools:
  1. get_hcp_risk_profile      — risk scores + key feature metrics for one HCP
  2. get_rule_flags            — fired flags with policy citations from rules.json
  3. get_peer_benchmark        — percentile rank vs specialty peers
  4. get_top_anomalous_features — top IF-driving features for one HCP

All tools load parquets lazily (module-level cache, loaded on first call).
All tools return JSON-serialisable dicts. On any error: {"error": "..."}.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from langchain.tools import tool

# ── Paths (resolved relative to project root) ─────────────────────────────────

_ROOT = Path(__file__).resolve().parents[2]

_PATHS = {
    "risk_scores":        _ROOT / "models/outputs/risk_scores.parquet",
    "rule_flags":         _ROOT / "models/outputs/rule_flags.parquet",
    "if_scores":          _ROOT / "models/outputs/if_scores.parquet",
    "feature_store_raw":  _ROOT / "features/outputs/feature_store_raw.parquet",
    "hcp_spend_raw":      _ROOT / "features/outputs/hcp_spend_raw_dollars.parquet",
    "feature_importance": _ROOT / "models/outputs/feature_importance.csv",
    "shap_values":        _ROOT / "models/outputs/shap_values.parquet",
    "rules_json":         _ROOT / "compliance/rules.json",
}

# ── Module-level parquet cache ─────────────────────────────────────────────────

_CACHE: Dict[str, Any] = {}


def _load(key: str) -> Any:
    if key not in _CACHE:
        path = _PATHS[key]
        if key == "rules_json":
            with open(path) as f:
                _CACHE[key] = json.load(f)
        elif key == "feature_importance":
            _CACHE[key] = pd.read_csv(path)
        else:
            _CACHE[key] = pd.read_parquet(path)
    return _CACHE[key]


def _load_optional(key: str) -> Any:
    """Load a data file that may not exist yet. Returns None when absent."""
    if key not in _CACHE:
        path = _PATHS.get(key)
        if path is None or not path.exists():
            _CACHE[key] = None
        else:
            try:
                _CACHE[key] = pd.read_parquet(path)
            except Exception:
                _CACHE[key] = None
    return _CACHE[key]


# ── Flag → rule_id mapping ─────────────────────────────────────────────────────
# Maps rule_flags.parquet boolean column names to rules.json rule_ids.
# A flag may correspond to multiple rules; the first matching rule is used for citation.

_FLAG_RULE_MAP: Dict[str, list[str]] = {
    "flag_meal_limit_breach":          ["MEAL_001", "MEAL_002", "MEAL_003"],
    "flag_meal_chronic_breach":        ["MEAL_001", "MEAL_002", "MEAL_003"],
    "flag_meal_overage_severe":        ["MEAL_003", "MEAL_004"],
    "flag_annual_cap_breach_2022":     ["COMP_001"],
    "flag_annual_cap_breach_2023":     ["COMP_001"],
    "flag_annual_cap_breach_2024":     ["COMP_001"],
    "flag_near_cap_2024":              ["COMP_003"],
    "flag_chronic_near_cap":           ["COMP_003"],
    "flag_speaker_fmv_breach":         ["SPEAKER_001"],
    "flag_speaker_fmv_chronic":        ["SPEAKER_001"],
    "flag_repeat_speaker":             ["SPEAKER_002"],
    "flag_high_repeat_speaker":        ["SPEAKER_002"],
    "flag_low_attendance_pattern":     ["SPEAKER_004"],
    "flag_rapid_repeat_pattern":       ["SPEAKER_005"],
    "flag_missing_attestation":        ["ATTEST_001"],
    "flag_chronic_missing_attestation":["ATTEST_001"],
    "flag_vague_rationale":            ["ATTEST_002"],
    "flag_vague_rationale_pattern":    ["ATTEST_002"],
    "flag_fmv_non_compliance":         ["COMP_002", "ATTEST_003"],
    "flag_rep_concentration":          ["FREQ_003"],
    "flag_speaking_fee_concentration": ["SPEAKER_001"],
    "flag_escalating_spend":           ["COMP_001"],
    "flag_escalating_rank":            ["COMP_001"],
}

_FLAG_SEVERITY: Dict[str, str] = {
    "flag_meal_limit_breach":           "medium",
    "flag_meal_chronic_breach":         "high",
    "flag_meal_overage_severe":         "high",
    "flag_annual_cap_breach_2022":      "critical",
    "flag_annual_cap_breach_2023":      "critical",
    "flag_annual_cap_breach_2024":      "critical",
    "flag_near_cap_2024":               "high",
    "flag_chronic_near_cap":            "high",
    "flag_speaker_fmv_breach":          "high",
    "flag_speaker_fmv_chronic":         "critical",
    "flag_repeat_speaker":              "medium",
    "flag_high_repeat_speaker":         "high",
    "flag_low_attendance_pattern":      "high",
    "flag_rapid_repeat_pattern":        "medium",
    "flag_missing_attestation":         "medium",
    "flag_chronic_missing_attestation": "high",
    "flag_vague_rationale":             "medium",
    "flag_vague_rationale_pattern":     "high",
    "flag_fmv_non_compliance":          "high",
    "flag_rep_concentration":           "medium",
    "flag_speaking_fee_concentration":  "high",
    "flag_escalating_spend":            "medium",
    "flag_escalating_rank":             "medium",
}

# Features excluded from IF training — never surface as anomalous features
_IF_EXCLUDED = {
    "interaction_frequency_score",
    "data_completeness_score",
    "has_speaker_events",
    "has_cms_payments",
    "has_interactions",
}


# ── Tool 1: get_hcp_risk_profile ───────────────────────────────────────────────
@tool
def get_hcp_risk_profile(hcp_id: str) -> dict:
    """
    Retrieve the full risk profile for a single HCP by their hcp_id.

    Returns risk_score, risk_tier, rule_score, if_score, specialty, state,
    total_spend_usd, total_interactions, speaker_event_count, fmv_compliance_rate,
    vague_rationale_rate, meal_violations, unique_reps_interacted, and
    interaction_frequency_score.

    Use this tool first when investigating any HCP.
    """
    # Defensive clean: strip whitespace, quotes, and any compound LLM input
    hcp_id = hcp_id.strip().strip("'\"")
    if "," in hcp_id:
        hcp_id = hcp_id.split(",")[0].strip().strip("'\"")

    def _safe_int(val, default: int = 0) -> int:
        """Cast to int, returning default on None/NaN/error."""
        try:
            if val is None:
                return default
            f = float(val)
            return default if (f != f) else int(f)  # f != f is True only for NaN
        except (TypeError, ValueError):
            return default

    def _safe_float(val, default: float = 0.0, ndigits: int = 4) -> float:
        """Cast to float, returning default on None/NaN/error."""
        try:
            if val is None:
                return default
            f = float(val)
            return default if (f != f) else round(f, ndigits)
        except (TypeError, ValueError):
            return default

    try:
        risk  = _load("risk_scores")
        raw   = _load("feature_store_raw")
        spend = _load("hcp_spend_raw")

        row_risk = risk[risk["hcp_id"] == hcp_id]
        if row_risk.empty:
            return {"error": f"HCP '{hcp_id}' not found in risk_scores.parquet"}

        row_raw   = raw[raw["hcp_id"] == hcp_id]
        row_spend = spend[spend["hcp_id"] == hcp_id]

        r = row_risk.iloc[0]

        # Total spend across all years
        total_spend = 0.0
        if not row_spend.empty:
            s = row_spend.iloc[0]
            total_spend = (
                _safe_float(s.get("spend_2022", 0.0))
                + _safe_float(s.get("spend_2023", 0.0))
                + _safe_float(s.get("spend_2024", 0.0))
            )

        # Interaction + identity features from feature_store_raw
        feat: Dict[str, Any] = {}
        if not row_raw.empty:
            fr = row_raw.iloc[0]
            total_int = _safe_int(fr.get("total_interactions", 0))
            vague_int = _safe_int(fr.get("interactions_with_vague_rationale", 0))
            feat = {
                "specialty":                   fr.get("specialty"),
                "state":                       fr.get("state"),
                "total_interactions":          total_int,
                "speaker_event_count":         _safe_int(fr.get("total_events_as_speaker", 0)),
                "fmv_compliance_rate":         _safe_float(fr.get("fmv_compliance_rate", 1.0)),
                "vague_rationale_rate":        round(vague_int / total_int, 4) if total_int > 0 else 0.0,
                "meal_violations":             _safe_int(fr.get("meals_over_limit_count", 0)),
                "unique_reps_interacted":      _safe_int(fr.get("unique_reps_interacted", 0)),
                "interaction_frequency_score": _safe_float(fr.get("interaction_frequency_score", 0.0), ndigits=2),
            }

        return {
            "hcp_id":           hcp_id,
            "risk_score":       _safe_float(r["risk_score"], ndigits=2),
            "risk_tier":        str(r["risk_tier"]),
            "rule_score":       _safe_float(r["rule_score"], ndigits=2),
            "if_score":         _safe_float(r["anomaly_score"], ndigits=2),
            "total_rule_flags": _safe_int(r["total_rule_flags"]),
            "most_severe_flag": str(r["most_severe_flag"]),
            "total_spend_usd":  round(total_spend, 2),
            **feat,
        }

    except Exception as e:
        return {"error": str(e)}


# ── Tool 2: get_rule_flags ─────────────────────────────────────────────────────

@tool
def get_rule_flags(hcp_id: str) -> dict:
    """
    Retrieve all compliance rule flags that fired for a single HCP.

    Returns only fired flags (flag_value == 1) with severity and policy citations
    matched from compliance/rules.json. Includes the rule threshold and the
    source policy chunk_id for audit purposes.

    Use this tool to understand which specific compliance rules an HCP violated.
    """
    # Add this as the FIRST line inside every tool function
    hcp_id = hcp_id.strip().strip("'\"")
    try:
        flags_df = _load("rule_flags")
        rules_data = _load("rules_json")

        row = flags_df[flags_df["hcp_id"] == hcp_id]
        if row.empty:
            return {"error": f"HCP '{hcp_id}' not found in rule_flags.parquet"}

        # Build rule_id → rule lookup
        rules_by_id = {r["rule_id"]: r for r in rules_data["rules"]}

        fr = row.iloc[0]
        flag_cols = [c for c in flags_df.columns if c.startswith("flag_") and flags_df[c].dtype == bool]

        fired = []
        for col in flag_cols:
            if not bool(fr[col]):
                continue

            rule_ids = _FLAG_RULE_MAP.get(col, [])
            threshold = None
            citation = None

            # Use first matching rule for citation + threshold
            for rid in rule_ids:
                rule = rules_by_id.get(rid)
                if rule:
                    threshold = rule.get("effective_threshold")
                    # Build citation from first source chunk
                    sources = rule.get("sources", [])
                    if sources:
                        chunk = sources[0].get("chunk_id", "")
                        authority = sources[0].get("authority", "")
                        citation = f"{rid}: {rule['rule_name']} [{authority} — {chunk}]"
                    break

            fired.append({
                "flag_name":       col,
                "flag_value":      1.0,
                "threshold":       threshold,
                "policy_citation": citation,
                "severity":        _FLAG_SEVERITY.get(col, "medium"),
            })

        return {
            "hcp_id":          hcp_id,
            "total_flags":     len(fired),
            "most_severe":     str(fr.get("most_severe_flag", "none")),
            "fired_flags":     fired,
        }

    except Exception as e:
        return {"error": str(e)}


# ── Tool 3: get_peer_benchmark ─────────────────────────────────────────────────

@tool
def get_peer_benchmark(hcp_id: str) -> dict:
    """
    Compare an HCP's total spend against specialty peers.

    Returns percentile_rank (0–100), peer_avg_total_spend, peer_max_total_spend,
    hcp_total_spend, specialty, and state. If fewer than 10 specialty peers exist,
    falls back to the full population.

    Use this tool to contextualise whether an HCP's spend level is unusual
    relative to their peers.
    """
    hcp_id = hcp_id.strip().strip("'\"")
    try:
        raw   = _load("feature_store_raw")
        spend = _load("hcp_spend_raw")

        row_raw   = raw[raw["hcp_id"] == hcp_id]
        row_spend = spend[spend["hcp_id"] == hcp_id]

        if row_raw.empty:
            return {"error": f"HCP '{hcp_id}' not found in feature_store_raw.parquet"}
        if row_spend.empty:
            return {"error": f"HCP '{hcp_id}' not found in hcp_spend_raw_dollars.parquet"}

        fr = row_raw.iloc[0]
        sr = row_spend.iloc[0]

        specialty = fr.get("specialty")
        state     = fr.get("state")

        # Compute total spend for all HCPs
        total_col = spend["spend_2022"].fillna(0) + spend["spend_2023"].fillna(0) + spend["spend_2024"].fillna(0)
        spend_with_total = spend.copy()
        spend_with_total["total_spend"] = total_col

        hcp_total = float(sr.get("spend_2022", 0) + sr.get("spend_2023", 0) + sr.get("spend_2024", 0))

        # Specialty filter — fallback to population if < 10 peers
        if specialty and specialty != "None":
            peers_raw = raw[raw["specialty"] == specialty]
            peer_ids  = peers_raw["hcp_id"].values
            peers_spend = spend_with_total[spend_with_total["hcp_id"].isin(peer_ids)]
        else:
            peers_spend = spend_with_total

        if len(peers_spend) < 10:
            peers_spend = spend_with_total

        peer_totals = peers_spend["total_spend"].values
        percentile  = float(np.mean(peer_totals <= hcp_total) * 100)

        return {
            "hcp_id":              hcp_id,
            "percentile_rank":     round(percentile, 1),
            "peer_avg_total_spend": round(float(peer_totals.mean()), 2),
            "peer_max_total_spend": round(float(peer_totals.max()), 2),
            "hcp_total_spend":     round(hcp_total, 2),
            "peer_count":          len(peers_spend),
            "specialty":           str(specialty) if specialty else None,
            "state":               str(state) if state else None,
            "fallback_used":       len(peers_spend) == len(spend_with_total),
        }

    except Exception as e:
        return {"error": str(e)}


# ── Tool 4: get_top_anomalous_features ────────────────────────────────────────

@tool
def get_top_anomalous_features(hcp_id: str) -> dict:
    """
    Retrieve the top anomaly-driving features for a specific HCP.

    When shap_values.parquet is present (generated by models/isolation_forest.py),
    returns per-HCP SHAP TreeExplainer values: importance_score = abs(shap_value),
    direction = 'high' if shap_value > 0 else 'low'.

    Falls back to global Pearson |r| proxy from feature_importance.csv when
    shap_values.parquet is absent. In both cases excludes features that were
    not used in Isolation Forest training.

    Always returns top 5 features.
    """
    hcp_id = hcp_id.strip().strip("'\"")
    if "," in hcp_id:
        hcp_id = hcp_id.split(",")[0].strip().strip("'\"")
    try:
        raw        = _load("feature_store_raw")
        shap_df    = _load_optional("shap_values")

        row = raw[raw["hcp_id"] == hcp_id]
        if row.empty:
            return {"error": f"HCP '{hcp_id}' not found in feature_store_raw.parquet"}

        fr    = row.iloc[0]
        top_n = 5

        # ── Path A: per-HCP SHAP values ──────────────────────────────────────
        if shap_df is not None:
            shap_row = shap_df[shap_df["hcp_id"] == hcp_id]
            if not shap_row.empty:
                sr           = shap_row.iloc[0]
                feature_cols = [c for c in shap_df.columns if c != "hcp_id"]
                # Filter excluded features
                feature_cols = [c for c in feature_cols if c not in _IF_EXCLUDED]

                # Sort by abs(SHAP) descending
                shap_vals   = {c: float(sr[c]) for c in feature_cols}
                ranked      = sorted(shap_vals.items(), key=lambda kv: abs(kv[1]), reverse=True)

                results = []
                for feat, shap_val in ranked:
                    hcp_val = fr.get(feat)
                    if hcp_val is None or (isinstance(hcp_val, float) and np.isnan(hcp_val)):
                        continue

                    results.append({
                        "feature_name":     feat,
                        "hcp_value":        round(float(hcp_val), 4),
                        "importance_score": round(abs(shap_val), 6),
                        "pearson_r":        round(abs(shap_val), 6),
                        "direction":        "high" if shap_val > 0 else "low",
                    })

                    if len(results) >= top_n:
                        break

                if results:
                    return {
                        "hcp_id":   hcp_id,
                        "top_n":    len(results),
                        "note":     "SHAP TreeExplainer per-HCP values",
                        "features": results,
                    }
            # shap row missing for this hcp_id — fall through to Pearson

        # ── Path B: global Pearson |r| fallback ──────────────────────────────
        fi         = _load("feature_importance")
        fi_filtered = fi[~fi["feature"].isin(_IF_EXCLUDED)].head(top_n * 3)

        results = []
        for _, fi_row in fi_filtered.iterrows():
            feat = fi_row["feature"]
            if feat not in raw.columns:
                continue

            hcp_val = fr.get(feat)
            if hcp_val is None or (isinstance(hcp_val, float) and np.isnan(hcp_val)):
                continue

            pop_mean  = float(raw[feat].mean())
            hcp_val   = float(hcp_val)
            direction = "high" if hcp_val > pop_mean else "low"

            entry = {
                "feature_name":     feat,
                "hcp_value":        round(hcp_val, 4),
                "population_mean":  round(pop_mean, 4),
                "importance_score": round(float(fi_row["mean_abs_score_diff"]), 4),
                "pearson_r":        round(float(fi_row["mean_abs_score_diff"]), 4),
                "direction":        direction,
            }
            if hcp_val < -1.0:
                entry["note"] = "value may be z-score scaled — check feature_store_raw source"

            results.append(entry)
            if len(results) >= top_n:
                break

        return {
            "hcp_id":   hcp_id,
            "top_n":    len(results),
            "note":     "Pearson |r| proxy importance (run models/isolation_forest.py to generate SHAP)",
            "features": results,
        }

    except Exception as e:
        return {"error": str(e)}
