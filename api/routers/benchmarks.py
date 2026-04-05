"""
api/routers/benchmarks.py — Peer benchmark endpoint

GET /benchmarks/{hcp_id} — Peer comparison for a single HCP
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
import pandas as pd

from api.dependencies import get_risk_scores, get_rule_flags

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get("/{hcp_id}")
def get_benchmark(
    hcp_id: str,
    risk_scores: pd.DataFrame = Depends(get_risk_scores),
    rule_flags:  pd.DataFrame = Depends(get_rule_flags),
):
    """
    Return peer benchmark statistics for a single HCP.

    Peers are defined as HCPs sharing the same specialty and state.
    Falls back to full population if specialty/state is None or the
    peer group has fewer than 5 members.

    Spend figures come from rule_flags.spend_2024 (total NovaPharma spend);
    peer grouping metadata (specialty, state) comes from risk_scores.
    """
    rs_row = risk_scores[risk_scores["hcp_id"] == hcp_id]
    if rs_row.empty:
        raise HTTPException(status_code=404, detail=f"HCP '{hcp_id}' not found")

    r         = rs_row.iloc[0]
    specialty = r.get("specialty") if "specialty" in r.index else None
    state     = r.get("state")     if "state"     in r.index else None

    # Spend comes from rule_flags (has spend_2022/2023/2024)
    rf_row    = rule_flags[rule_flags["hcp_id"] == hcp_id]
    spend_col = "spend_2024" if "spend_2024" in rule_flags.columns else "peak_year_spend"
    if spend_col not in rule_flags.columns:
        spend_col = None

    hcp_spend = 0.0
    if spend_col and not rf_row.empty:
        raw = rf_row.iloc[0].get(spend_col)
        hcp_spend = float(raw) if raw is not None and not pd.isna(raw) else 0.0

    # Build peer group from risk_scores for specialty/state metadata
    peers_rs = risk_scores.copy()
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

    return {
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
    }
