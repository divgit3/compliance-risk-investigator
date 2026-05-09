# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
agents/tools/monitoring_tools.py — LangChain tools for population-level compliance
monitoring queries (Task 3.2).

Tools:
  1. get_risk_distribution      — tier counts + stats for filtered population
  2. get_flag_patterns          — top flags by HCP count in scope
  3. get_high_risk_segments     — specialty/state segments ranked by critical rate
  4. detect_systemic_issues     — deterministic systemic risk pattern detection

All tools use the same module-level _CACHE dict pattern as data_tools.py.
All tools return JSON-serializable dicts. On any error: {"error": "..."}.

NOTE ON DATA LIMITATIONS (known from Phase 2):
  - specialty: ALL NULL in DuckDB dev — specialty filter always returns empty
  - np_escalating_rank: 0-filled — not used in any calculation here
  - engagement_priority_score: capped at 45pts — not used here
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from langchain.tools import tool

# ── Paths ──────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[2]

_PATHS = {
    "risk_scores":        _ROOT / "models/outputs/risk_scores.parquet",
    "rule_flags":         _ROOT / "models/outputs/rule_flags.parquet",
    "feature_store_raw":  _ROOT / "features/outputs/feature_store_raw.parquet",
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
        else:
            _CACHE[key] = pd.read_parquet(path)
    return _CACHE[key]


# ── Flag metadata (mirrors data_tools.py for consistent citations) ─────────────

_FLAG_RULE_MAP: Dict[str, list[str]] = {
    "flag_meal_limit_breach":           ["MEAL_001", "MEAL_002", "MEAL_003"],
    "flag_meal_chronic_breach":         ["MEAL_001", "MEAL_002", "MEAL_003"],
    "flag_meal_overage_severe":         ["MEAL_003", "MEAL_004"],
    "flag_annual_cap_breach_2022":      ["COMP_001"],
    "flag_annual_cap_breach_2023":      ["COMP_001"],
    "flag_annual_cap_breach_2024":      ["COMP_001"],
    "flag_near_cap_2024":               ["COMP_003"],
    "flag_chronic_near_cap":            ["COMP_003"],
    "flag_speaker_fmv_breach":          ["SPEAKER_001"],
    "flag_speaker_fmv_chronic":         ["SPEAKER_001"],
    "flag_repeat_speaker":              ["SPEAKER_002"],
    "flag_high_repeat_speaker":         ["SPEAKER_002"],
    "flag_low_attendance_pattern":      ["SPEAKER_004"],
    "flag_rapid_repeat_pattern":        ["SPEAKER_005"],
    "flag_missing_attestation":         ["ATTEST_001"],
    "flag_chronic_missing_attestation": ["ATTEST_001"],
    "flag_vague_rationale":             ["ATTEST_002"],
    "flag_vague_rationale_pattern":     ["ATTEST_002"],
    "flag_fmv_non_compliance":          ["COMP_002", "ATTEST_003"],
    "flag_rep_concentration":           ["FREQ_003"],
    "flag_speaking_fee_concentration":  ["SPEAKER_001"],
    "flag_escalating_spend":            ["COMP_001"],
    "flag_escalating_rank":             ["COMP_001"],
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


def _build_policy_citation(flag_name: str, rules_data: dict) -> str | None:
    """Return 'RULE_ID: Rule Name [Authority — chunk_id]' for the first matching rule."""
    rules_by_id = {r["rule_id"]: r for r in rules_data["rules"]}
    for rid in _FLAG_RULE_MAP.get(flag_name, []):
        rule = rules_by_id.get(rid)
        if rule:
            sources = rule.get("sources", [])
            if sources:
                chunk    = sources[0].get("chunk_id", "")
                authority = sources[0].get("authority", "")
                return f"{rid}: {rule['rule_name']} [{authority} — {chunk}]"
    return None


def _apply_scope_filter(
    risk_df: pd.DataFrame,
    raw_df: pd.DataFrame,
    specialty: str,
    state: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Merge risk_scores with feature_store_raw and apply specialty/state filters.

    Returns (filtered_risk_df, merged_df) where merged_df has risk + raw columns.
    Raises ValueError if specialty filter produces zero rows.
    """
    merged = risk_df.merge(raw_df[["hcp_id", "specialty", "state"]], on="hcp_id", how="left")

    if specialty:
        # specialty is None for all HCPs in DuckDB dev — warn but don't crash
        spec_mask = merged["specialty"].fillna("").str.lower() == specialty.lower()
        if not spec_mask.any():
            raise ValueError(
                f"No HCPs found for specialty='{specialty}'. "
                "Note: specialty is NULL for all HCPs in DuckDB dev environment."
            )
        merged = merged[spec_mask]

    if state:
        state_mask = merged["state"].fillna("").str.upper() == state.upper()
        if not state_mask.any():
            raise ValueError(f"No HCPs found for state='{state}'.")
        merged = merged[state_mask]

    filtered_risk = risk_df[risk_df["hcp_id"].isin(merged["hcp_id"])]
    return filtered_risk, merged


# ── Tool 1: get_risk_distribution ─────────────────────────────────────────────

@tool
def get_risk_distribution(specialty: str = "", state: str = "") -> dict:
    """
    Compute risk tier distribution across the HCP population (or a filtered segment).

    Optionally filter by specialty or state. Returns counts and percentages for
    each risk tier (critical/high/medium/low), plus avg and median risk scores.

    Use this tool first to establish the risk baseline before drilling into flags
    or segments.
    """
    try:
        risk = _load("risk_scores")
        raw  = _load("feature_store_raw")

        if specialty or state:
            risk_filtered, _ = _apply_scope_filter(risk, raw, specialty, state)
        else:
            risk_filtered = risk

        total = len(risk_filtered)
        if total == 0:
            return {"error": "No HCPs in scope after applying filters."}

        tier_counts: Dict[str, int] = {
            t: int((risk_filtered["risk_tier"] == t).sum())
            for t in ("critical", "high", "medium", "low")
        }

        scores = risk_filtered["risk_score"].dropna().values

        return {
            "specialty_filter": specialty or None,
            "state_filter":     state or None,
            "total_count":      total,
            "critical_count":   tier_counts["critical"],
            "high_count":       tier_counts["high"],
            "medium_count":     tier_counts["medium"],
            "low_count":        tier_counts["low"],
            "critical_pct":     round(tier_counts["critical"] / total, 4),
            "high_pct":         round(tier_counts["high"] / total, 4),
            "medium_pct":       round(tier_counts["medium"] / total, 4),
            "low_pct":          round(tier_counts["low"] / total, 4),
            "avg_risk_score":   round(float(scores.mean()), 2),
            "median_risk_score":round(float(np.median(scores)), 2),
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"get_risk_distribution failed: {e}"}


# ── Tool 2: get_flag_patterns ──────────────────────────────────────────────────

@tool
def get_flag_patterns(specialty: str = "", state: str = "", top_n: int = 10) -> dict:
    """
    Identify the most common compliance flags across the HCP population or a segment.

    Counts distinct HCPs with each flag fired (not raw flag instances). Returns
    the top_n flags sorted by rate (count / total_hcps_in_scope) with policy
    citations from rules.json.

    Use this tool to understand which compliance rules are being broken most often.
    """
    try:
        risk       = _load("risk_scores")
        flags_df   = _load("rule_flags")
        raw        = _load("feature_store_raw")
        rules_data = _load("rules_json")

        # Determine in-scope HCP IDs
        if specialty or state:
            _, merged = _apply_scope_filter(risk, raw, specialty, state)
            scope_ids = set(merged["hcp_id"].unique())
            flags_scope = flags_df[flags_df["hcp_id"].isin(scope_ids)]
        else:
            scope_ids   = set(risk["hcp_id"].unique())
            flags_scope = flags_df

        total_in_scope = len(scope_ids)
        if total_in_scope == 0:
            return {"error": "No HCPs in scope."}

        flag_cols = [c for c in flags_df.columns if c.startswith("flag_") and flags_df[c].dtype == bool]

        results = []
        for col in flag_cols:
            count = int(flags_scope[col].sum())
            if count == 0:
                continue
            rate    = count / total_in_scope
            citation = _build_policy_citation(col, rules_data)
            results.append({
                "flag_name":       col,
                "count":           count,
                "rate":            round(rate, 4),
                "policy_citation": citation,
                "severity":        _FLAG_SEVERITY.get(col, "medium"),
            })

        results.sort(key=lambda x: x["rate"], reverse=True)

        return {
            "specialty_filter":    specialty or None,
            "state_filter":        state or None,
            "total_hcps_in_scope": total_in_scope,
            "flags":               results[:top_n],
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"get_flag_patterns failed: {e}"}


# ── Tool 3: get_high_risk_segments ─────────────────────────────────────────────

@tool
def get_high_risk_segments(segment_type: str = "both", top_n: int = 5) -> dict:
    """
    Identify specialties and/or states with the highest critical and high-tier
    HCP rates.

    segment_type: "specialty" | "state" | "both" (default)
    top_n: number of top segments to return per type (default 5)

    Segments with fewer than 5 HCPs are excluded to avoid false alarms on
    very small groups. Returns critical_rate, high_rate, avg_risk_score,
    and the most common flag fired within each segment.

    Note: specialty is NULL for all HCPs in DuckDB dev — specialty segments
    will be empty unless real specialty data is available.
    """
    try:
        risk     = _load("risk_scores")
        raw      = _load("feature_store_raw")
        flags_df = _load("rule_flags")

        merged = risk.merge(
            raw[["hcp_id", "specialty", "state"]], on="hcp_id", how="left"
        )

        flag_cols = [c for c in flags_df.columns if c.startswith("flag_") and flags_df[c].dtype == bool]

        def _top_flag_for_ids(hcp_ids: set) -> str:
            """Return the flag fired by the most HCPs in this set."""
            sub = flags_df[flags_df["hcp_id"].isin(hcp_ids)]
            counts = {col: int(sub[col].sum()) for col in flag_cols if sub[col].sum() > 0}
            if not counts:
                return "none"
            return max(counts, key=counts.__getitem__)

        def _build_segments(group_col: str) -> list[dict]:
            segs = []
            for val, grp in merged.groupby(group_col):
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    continue
                if len(grp) < 5:
                    continue
                hcp_ids = set(grp["hcp_id"].unique())
                crit_n  = int((grp["risk_tier"] == "critical").sum())
                high_n  = int((grp["risk_tier"] == "high").sum())
                total_n = len(grp)
                segs.append({
                    "segment_type":   group_col,
                    "segment_value":  str(val),
                    "hcp_count":      total_n,
                    "critical_count": crit_n,
                    "high_count":     high_n,
                    "critical_rate":  round(crit_n / total_n, 4),
                    "high_rate":      round(high_n / total_n, 4),
                    "avg_risk_score": round(float(grp["risk_score"].mean()), 2),
                    "top_flag":       _top_flag_for_ids(hcp_ids),
                })
            segs.sort(key=lambda x: (x["critical_rate"], x["high_rate"]), reverse=True)
            return segs[:top_n]

        result: dict[str, Any] = {}

        if segment_type in ("specialty", "both"):
            result["specialty_segments"] = _build_segments("specialty")

        if segment_type in ("state", "both"):
            result["state_segments"] = _build_segments("state")

        return result

    except Exception as e:
        return {"error": f"get_high_risk_segments failed: {e}"}


# ── Tool 4: detect_systemic_issues ────────────────────────────────────────────

@tool
def detect_systemic_issues(specialty: str = "", state: str = "") -> dict:
    """
    Detect population-level compliance patterns that suggest systemic risk rather
    than isolated individual violations.

    Applies four deterministic rules (no LLM involved):
      1. critical_cluster     — any specialty/state with >2% critical rate AND >=10 HCPs
      2. high_flag_rate_specialty — any specialty where >30% of HCPs have >=2 flags AND >=10 HCPs
      3. high_flag_rate_state    — any state where >25% of HCPs have >=2 flags AND >=10 HCPs
      4. dominant_flag_pattern   — any single flag accounts for >40% of all fired flag instances

    All recommendations are templated strings — not LLM-generated.
    Use this tool to surface issues requiring escalation to compliance leadership.
    """
    try:
        risk     = _load("risk_scores")
        raw      = _load("feature_store_raw")
        flags_df = _load("rule_flags")

        merged = risk.merge(
            raw[["hcp_id", "specialty", "state"]], on="hcp_id", how="left"
        ).merge(
            flags_df[["hcp_id"] + [c for c in flags_df.columns if c.startswith("flag_")]],
            on="hcp_id",
            how="left",
        )

        # Apply optional scope filter
        if specialty:
            mask = merged["specialty"].fillna("").str.lower() == specialty.lower()
            if not mask.any():
                return {
                    "issues": [],
                    "message": (
                        f"No HCPs found for specialty='{specialty}'. "
                        "Note: specialty is NULL for all HCPs in DuckDB dev."
                    ),
                }
            merged = merged[mask]

        if state:
            mask = merged["state"].fillna("").str.upper() == state.upper()
            if not mask.any():
                return {"issues": [], "message": f"No HCPs found for state='{state}'."}
            merged = merged[mask]

        flag_cols = [c for c in merged.columns if c.startswith("flag_") and merged[c].dtype == object or
                     c.startswith("flag_") and merged[c].dtype == bool]
        flag_cols = [c for c in merged.columns if c.startswith("flag_")]
        # Ensure boolean dtype
        for col in flag_cols:
            if merged[col].dtype != bool:
                merged[col] = merged[col].astype(bool)

        merged["multi_flag"] = merged[flag_cols].astype(int).sum(axis=1) >= 2

        issues = []

        # ── Issue 1 & 2: Specialty-level checks ───────────────────────────────
        # (Will produce no results when specialty=None for all HCPs — by design)
        for grp_col, multi_threshold, issue_type, sev, rec_tmpl in [
            ("specialty", 0.30, "high_flag_rate_specialty", "high",
             "Review training compliance for {val} reps"),
        ]:
            for val, grp in merged.groupby(grp_col):
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    continue
                n = len(grp)
                if n < 10:
                    continue

                # critical_cluster check
                crit_rate = (grp["risk_tier"] == "critical").sum() / n
                if crit_rate > 0.02:
                    top_flags_here = [
                        c for c in sorted(flag_cols, key=lambda c: -int(grp[c].sum()))
                        if grp[c].sum() > 0
                    ][:3]
                    issues.append({
                        "issue_type":         "critical_cluster",
                        "description":        (
                            f"{val} has {crit_rate:.1%} critical-tier HCPs "
                            f"({int(crit_rate*n)}/{n})."
                        ),
                        "affected_hcp_count": int(crit_rate * n),
                        "severity":           "critical",
                        "top_flags":          top_flags_here,
                        "recommendation":     f"Initiate territory-level audit for {val}",
                    })

                # high_flag_rate check
                multi_rate = grp["multi_flag"].mean()
                if multi_rate > multi_threshold:
                    top_flags_here = [
                        c for c in sorted(flag_cols, key=lambda c: -int(grp[c].sum()))
                        if grp[c].sum() > 0
                    ][:3]
                    issues.append({
                        "issue_type":         issue_type,
                        "description":        (
                            f"{val} has {multi_rate:.1%} of HCPs with ≥2 compliance flags "
                            f"({int(multi_rate*n)}/{n})."
                        ),
                        "affected_hcp_count": int(multi_rate * n),
                        "severity":           sev,
                        "top_flags":          top_flags_here,
                        "recommendation":     rec_tmpl.format(val=val),
                    })

        # ── Issue 3: State-level high_flag_rate ────────────────────────────────
        for val, grp in merged.groupby("state"):
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            n = len(grp)
            if n < 10:
                continue

            # critical_cluster by state
            crit_rate = (grp["risk_tier"] == "critical").sum() / n
            if crit_rate > 0.02:
                top_flags_here = [
                    c for c in sorted(flag_cols, key=lambda c: -int(grp[c].sum()))
                    if grp[c].sum() > 0
                ][:3]
                issues.append({
                    "issue_type":         "critical_cluster",
                    "description":        (
                        f"State {val} has {crit_rate:.1%} critical-tier HCPs "
                        f"({int(crit_rate*n)}/{n})."
                    ),
                    "affected_hcp_count": int(crit_rate * n),
                    "severity":           "critical",
                    "top_flags":          top_flags_here,
                    "recommendation":     f"Initiate territory-level audit for state {val}",
                })

            # high_flag_rate by state
            multi_rate = grp["multi_flag"].mean()
            if multi_rate > 0.25:
                top_flags_here = [
                    c for c in sorted(flag_cols, key=lambda c: -int(grp[c].sum()))
                    if grp[c].sum() > 0
                ][:3]
                issues.append({
                    "issue_type":         "high_flag_rate_state",
                    "description":        (
                        f"State {val} has {multi_rate:.1%} of HCPs with ≥2 compliance flags "
                        f"({int(multi_rate*n)}/{n})."
                    ),
                    "affected_hcp_count": int(multi_rate * n),
                    "severity":           "high",
                    "top_flags":          top_flags_here,
                    "recommendation":     f"Escalate to regional compliance officer for {val}",
                })

        # ── Issue 4: Dominant flag pattern ─────────────────────────────────────
        total_fired = sum(int(merged[c].sum()) for c in flag_cols)
        if total_fired > 0:
            for col in flag_cols:
                col_fired = int(merged[col].sum())
                share = col_fired / total_fired
                if share > 0.40:
                    issues.append({
                        "issue_type":         "dominant_flag_pattern",
                        "description":        (
                            f"'{col}' accounts for {share:.1%} of all fired flag instances "
                            f"({col_fired:,} of {total_fired:,}). "
                            "This suggests a systemic policy gap rather than isolated violations."
                        ),
                        "affected_hcp_count": col_fired,
                        "severity":           "medium",
                        "top_flags":          [col],
                        "recommendation":     (
                            f"Review {col} policy training across organization"
                        ),
                    })

        if not issues:
            return {
                "issues":  [],
                "message": "No systemic issues detected above configured thresholds.",
            }

        return {"issues": issues}

    except Exception as e:
        return {"error": f"detect_systemic_issues failed: {e}"}
