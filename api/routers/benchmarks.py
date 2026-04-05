"""
api/routers/benchmarks.py — Peer benchmark endpoint (Task 3.4 / Task 3.5)

GET /benchmarks/{hcp_id} — Peer comparison + industry benchmarks for one HCP

Task 3.4: percentile_rank, peer_avg/max/count from risk_scores + rule_flags
Task 3.5: sow, industry_ratio, engagement_priority_score_full,
          competitor_avg_spend, population_avg_spend from
          features/outputs/{competitor,population}_benchmarks.parquet
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
import numpy as np
import pandas as pd

from api.dependencies import (
    get_competitor_benchmarks,
    get_population_benchmarks,
    get_risk_scores,
    get_rule_flags,
)

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])

_ATHENA_LIMITATION = (
    "Athena not reachable — competitor benchmarks unavailable, "
    "engagement_priority_score capped at 45pts"
)


@router.get("/{hcp_id}")
def get_benchmark(
    hcp_id: str,
    risk_scores:          pd.DataFrame          = Depends(get_risk_scores),
    rule_flags:           pd.DataFrame          = Depends(get_rule_flags),
    competitor_bm: Optional[pd.DataFrame]       = Depends(get_competitor_benchmarks),
    population_bm: Optional[pd.DataFrame]       = Depends(get_population_benchmarks),
):
    """
    Return peer benchmark statistics and industry context for a single HCP.

    Peer spend figures come from rule_flags.spend_2024 (Nova Pharma spend).
    Industry benchmark fields (sow, industry_ratio, engagement_priority_score)
    come from competitor_benchmarks.parquet and population_benchmarks.parquet
    produced by features/industry_benchmarks.py (Task 3.5).  When those files
    are absent, the fields are None and data_limitations is populated.
    """
    rs_row = risk_scores[risk_scores["hcp_id"] == hcp_id]
    if rs_row.empty:
        raise HTTPException(status_code=404, detail=f"HCP '{hcp_id}' not found")

    r         = rs_row.iloc[0]
    specialty = r.get("specialty") if "specialty" in r.index else None
    state     = r.get("state")     if "state"     in r.index else None

    # ── Nova spend (from rule_flags, which has raw spend columns) ─────────────
    rf_row    = rule_flags[rule_flags["hcp_id"] == hcp_id]
    spend_col = "spend_2024" if "spend_2024" in rule_flags.columns else None
    hcp_spend = 0.0
    if spend_col and not rf_row.empty:
        raw = rf_row.iloc[0].get(spend_col)
        hcp_spend = float(raw) if raw is not None and not pd.isna(raw) else 0.0

    # ── Peer group (by specialty + state from risk_scores) ────────────────────
    peers_rs   = risk_scores.copy()
    peer_label = "full population"
    if specialty and not pd.isna(specialty):
        sp_peers = peers_rs[peers_rs["specialty"] == specialty]
        if len(sp_peers) >= 5:
            peers_rs   = sp_peers
            peer_label = f"specialty={specialty}"
    if state and not pd.isna(state):
        st_peers = peers_rs[peers_rs["state"] == state]
        if len(st_peers) >= 5:
            peers_rs   = st_peers
            peer_label = f"{peer_label}, state={state}"

    peer_hcp_ids = set(peers_rs["hcp_id"])
    if spend_col:
        peers_spend = rule_flags[rule_flags["hcp_id"].isin(peer_hcp_ids)][spend_col].dropna()
    else:
        peers_spend = pd.Series(dtype=float)

    peer_avg    = float(peers_spend.mean())   if len(peers_spend) else 0.0
    peer_max    = float(peers_spend.max())    if len(peers_spend) else 0.0
    peer_median = float(peers_spend.median()) if len(peers_spend) else 0.0
    pct_rank    = float((peers_spend < hcp_spend).sum() / len(peers_spend) * 100) if len(peers_spend) else 0.0

    # ── Industry benchmarks (Task 3.5 — optional) ─────────────────────────────
    sow                         = None
    industry_ratio              = None
    engagement_priority_score   = None
    competitor_avg_spend        = None
    population_avg_spend        = None
    athena_available            = False
    data_limitations: list[str] = []

    if competitor_bm is not None:
        comp_row = competitor_bm[competitor_bm["hcp_id"] == hcp_id]
        if not comp_row.empty:
            cr = comp_row.iloc[0]
            raw_sow = cr.get("sow")
            sow = float(raw_sow) if raw_sow is not None and not _isnan(raw_sow) else None
            raw_ca  = cr.get("competitor_avg_spend")
            competitor_avg_spend = float(raw_ca) if raw_ca is not None and not _isnan(raw_ca) else None
            athena_available = bool(cr.get("athena_available", False))
    else:
        data_limitations.append(_ATHENA_LIMITATION)

    if population_bm is not None:
        pop_row = population_bm[population_bm["hcp_id"] == hcp_id]
        if not pop_row.empty:
            pr = pop_row.iloc[0]
            raw_ir  = pr.get("industry_ratio")
            industry_ratio = float(raw_ir) if raw_ir is not None and not _isnan(raw_ir) else None
            raw_pa  = pr.get("population_avg_spend")
            population_avg_spend = float(raw_pa) if raw_pa is not None and not _isnan(raw_pa) else None
            raw_eps = pr.get("engagement_priority_score_full")
            engagement_priority_score = float(raw_eps) if raw_eps is not None and not _isnan(raw_eps) else None
            # Prefer raw dollar spend from population_benchmarks over z-scored rule_flags value
            raw_nova = pr.get("nova_spend_2024")
            if raw_nova is not None and not _isnan(raw_nova):
                hcp_spend = float(raw_nova)
    else:
        data_limitations.append(_ATHENA_LIMITATION)

    if not athena_available and competitor_bm is not None:
        data_limitations.append(_ATHENA_LIMITATION)

    return {
        # ── Peer benchmark fields (Task 3.4) ──────────────────────────────
        "hcp_id":            hcp_id,
        "specialty":         specialty,
        "state":             state,
        "peer_group":        peer_label,
        "peer_count":        len(peers_rs),
        "hcp_spend":         round(hcp_spend, 2),
        "peer_avg_spend":    round(peer_avg, 2),
        "peer_max_spend":    round(peer_max, 2),
        "peer_median_spend": round(peer_median, 2),
        "percentile_rank":   round(pct_rank, 1),
        "spend_column_used": spend_col or "none",
        # ── Industry benchmark fields (Task 3.5) ──────────────────────────
        "sow":                       sow,
        "industry_ratio":            round(industry_ratio, 4) if industry_ratio is not None else None,
        "engagement_priority_score": round(engagement_priority_score, 2) if engagement_priority_score is not None else None,
        "competitor_avg_spend":      round(competitor_avg_spend, 2) if competitor_avg_spend is not None else None,
        "population_avg_spend":      round(population_avg_spend, 2) if population_avg_spend is not None else None,
        "athena_available":          athena_available,
        "data_limitations":          data_limitations,
    }


def _isnan(v) -> bool:
    try:
        return bool(np.isnan(v))
    except (TypeError, ValueError):
        return False
