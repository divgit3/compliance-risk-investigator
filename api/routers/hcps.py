"""
api/routers/hcps.py — HCP-related endpoints

GET /hcps                    — paginated list sorted by risk_score desc
GET /hcps/{hcp_id}           — single HCP risk profile
GET /hcps/{hcp_id}/investigate — full InvestigationReport (agent)
GET /hcps/{hcp_id}/flags      — rule flags for one HCP
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import pandas as pd

from api.dependencies import (
    get_investigation_agent,
    get_risk_scores,
    get_rule_flags,
    get_shap_values,
    get_tov_summary,
)

router = APIRouter(prefix="/hcps", tags=["hcps"])

# Boolean flag columns (from rule_flags.parquet)
_FLAG_COLS = [
    "flag_meal_limit_breach", "flag_meal_chronic_breach", "flag_meal_overage_severe",
    "flag_annual_cap_breach_2022", "flag_annual_cap_breach_2023", "flag_annual_cap_breach_2024",
    "flag_near_cap_2024", "flag_chronic_near_cap",
    "flag_speaker_fmv_breach", "flag_speaker_fmv_chronic",
    "flag_repeat_speaker", "flag_high_repeat_speaker",
    "flag_low_attendance_pattern", "flag_rapid_repeat_pattern",
    "flag_missing_attestation", "flag_chronic_missing_attestation",
    "flag_vague_rationale", "flag_vague_rationale_pattern",
    "flag_fmv_non_compliance",
    "flag_rep_concentration", "flag_speaking_fee_concentration",
    "flag_escalating_spend", "flag_escalating_rank",
]


@router.get("")
def list_hcps(
    limit:  int           = Query(50, ge=1, le=500),
    offset: int           = Query(0, ge=0),
    tier:   Optional[str] = Query(None, description="Filter by risk_tier"),
    state:  Optional[str] = Query(None),
    risk_scores: pd.DataFrame = Depends(get_risk_scores),
):
    """List HCPs sorted by risk_score descending."""
    df = risk_scores.copy()
    if tier:
        df = df[df["risk_tier"] == tier]
    if state:
        if "state" in df.columns:
            df = df[df["state"] == state]
    df = df.sort_values("risk_score", ascending=False)
    total = len(df)
    page = df.iloc[offset : offset + limit]

    cols = [
        "hcp_id", "risk_score", "risk_tier", "rule_score", "anomaly_score",
        "total_rule_flags", "critical_flags", "high_flags",
        "most_severe_flag", "specialty", "state", "primary_rep_id", "hcp_name",
    ]
    available = [c for c in cols if c in page.columns]
    records = page[available].replace({float("nan"): None}).to_dict(orient="records")

    return {"total": total, "offset": offset, "limit": limit, "hcps": records}


@router.get("/stats/specialty-tier")
def stats_specialty_tier(
    risk_scores: pd.DataFrame = Depends(get_risk_scores),
):
    """Per-specialty HCP counts by risk tier (server-side aggregation).
    
    Returns list of {specialty, tier, count} rows for chart rendering.
    """
    if "specialty" not in risk_scores.columns or "risk_tier" not in risk_scores.columns:
        return {"rows": []}
    
    # Group by specialty + tier
    grouped = (
        risk_scores
        .groupby(["specialty", "risk_tier"], dropna=True)
        .size()
        .reset_index(name="count")
    )
    
    rows = [
        {
            "specialty": str(r["specialty"]),
            "tier": str(r["risk_tier"]).capitalize(),
            "count": int(r["count"]),
        }
        for _, r in grouped.iterrows()
    ]
    return {"rows": rows}


@router.get("/stats/state-tier")
def stats_state_tier(
    risk_scores: pd.DataFrame = Depends(get_risk_scores),
):
    """Per-state HCP counts + critical+high counts (server-side aggregation).
    
    Returns list of {state, total, critical_high} rows for chart rendering.
    """
    if "state" not in risk_scores.columns or "risk_tier" not in risk_scores.columns:
        return {"rows": []}
    
    df = risk_scores[risk_scores["state"].notna()].copy()
    df["is_critical_high"] = df["risk_tier"].isin(["critical", "high"])
    
    grouped = (
        df.groupby("state")
        .agg(
            total=("risk_tier", "count"),
            critical_high=("is_critical_high", "sum"),
        )
        .reset_index()
    )
    
    rows = [
        {
            "state": str(r["state"]),
            "total": int(r["total"]),
            "critical_high": int(r["critical_high"]),
        }
        for _, r in grouped.iterrows()
    ]
    return {"rows": rows}


@router.get("/{hcp_id}")
def get_hcp(
    hcp_id: str,
    risk_scores: pd.DataFrame = Depends(get_risk_scores),
    tov_summary: Optional[pd.DataFrame] = Depends(get_tov_summary),
):
    """Return risk profile for a single HCP, joined with TOV data."""
    row = risk_scores[risk_scores["hcp_id"] == hcp_id]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"HCP '{hcp_id}' not found")
    record = row.iloc[0].replace({float("nan"): None}).to_dict()
    
    # Join TOV summary if available
    if tov_summary is not None:
        tov_row = tov_summary[tov_summary["hcp_id"] == hcp_id]
        if not tov_row.empty:
            tov_record = tov_row.iloc[0].replace({float("nan"): None}).to_dict()
            # Merge, preferring risk_scores values for shared keys
            for k, v in tov_record.items():
                if k not in record:
                    record[k] = v
    
    return record


@router.get("/{hcp_id}/investigate")
async def investigate_hcp(
    hcp_id:  str,
    risk_scores:  pd.DataFrame = Depends(get_risk_scores),
    agent = Depends(get_investigation_agent),
):
    """Run the InvestigationAgent for a single HCP. Returns InvestigationReport."""
    if risk_scores[risk_scores["hcp_id"] == hcp_id].empty:
        raise HTTPException(status_code=404, detail=f"HCP '{hcp_id}' not found")
    if agent is None:
        raise HTTPException(status_code=503, detail="Investigation agent not available — OPENAI_API_KEY not set")
    try:
        report = await agent.investigate(hcp_id)
        return report.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc


@router.get("/{hcp_id}/flags")
def get_hcp_flags(
    hcp_id: str,
    risk_scores: pd.DataFrame = Depends(get_risk_scores),
    rule_flags:  pd.DataFrame = Depends(get_rule_flags),
):
    """Return all rule flags that fired for a single HCP."""
    if risk_scores[risk_scores["hcp_id"] == hcp_id].empty:
        raise HTTPException(status_code=404, detail=f"HCP '{hcp_id}' not found")

    row = rule_flags[rule_flags["hcp_id"] == hcp_id]
    if row.empty:
        return {"hcp_id": hcp_id, "fired_flags": [], "total_flags": 0}

    r = row.iloc[0]
    fired = []
    for col in _FLAG_COLS:
        if col in r.index and r[col]:
            fired.append(col)

    # Also pull from risk_scores for metadata
    rs_row = risk_scores[risk_scores["hcp_id"] == hcp_id].iloc[0]

    return {
        "hcp_id":        hcp_id,
        "fired_flags":   fired,
        "total_flags":   int(rs_row.get("total_rule_flags", len(fired))),
        "critical_flags":int(rs_row.get("critical_flags", 0)),
        "high_flags":    int(rs_row.get("high_flags", 0)),
        "most_severe_flag": rs_row.get("most_severe_flag"),
    }


@router.get("/{hcp_id}/shap")
def get_hcp_shap(
    hcp_id: str,
    top_n: int = Query(10, ge=1, le=99, description="Return top N features by absolute SHAP value"),
    shap_values: pd.DataFrame = Depends(get_shap_values),
):
    """Return top SHAP feature contributions for a single HCP."""
    row = shap_values[shap_values["hcp_id"] == hcp_id]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"HCP '{hcp_id}' has no SHAP values")

    feature_shap = row.drop(columns=["hcp_id"]).iloc[0]
    sorted_abs = feature_shap.abs().sort_values(ascending=False)
    top_features = [
        {
            "feature": feat_name,
            "shap_value": float(feature_shap[feat_name]),
            "abs_shap_value": abs(float(feature_shap[feat_name])),
        }
        for feat_name in sorted_abs.index[:top_n]
    ]

    return {"hcp_id": hcp_id, "top_features": top_features, "total_features": len(feature_shap)}
