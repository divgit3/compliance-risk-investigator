# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
api/routers/events.py — Speaker event feature endpoints

GET /events              — paginated event-level feature aggregates per HCP
GET /events/{hcp_id}     — event features for a single HCP
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import pandas as pd

from api.dependencies import get_event_features, get_risk_scores

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(
    limit:  int           = Query(50, ge=1, le=500),
    offset: int           = Query(0, ge=0),
    has_fmv_breach: Optional[bool] = Query(None, description="Filter HCPs with speaker_fee_over_fmv_flag_sum > 0"),
    event_features: pd.DataFrame = Depends(get_event_features),
):
    """List event feature aggregates per HCP (one row per HCP)."""
    df = event_features.copy()
    if has_fmv_breach is not None:
        col = "speaker_fee_over_fmv_flag_sum"
        if col in df.columns:
            if has_fmv_breach:
                df = df[df[col] > 0]
            else:
                df = df[df[col] == 0]

    total = len(df)
    page  = df.iloc[offset : offset + limit]
    records = page.replace({float("nan"): None}).to_dict(orient="records")
    return {"total": total, "offset": offset, "limit": limit, "events": records}


@router.get("/{hcp_id}")
def get_events_for_hcp(
    hcp_id: str,
    risk_scores:    pd.DataFrame = Depends(get_risk_scores),
    event_features: pd.DataFrame = Depends(get_event_features),
):
    """Return event feature aggregates for a single HCP."""
    if risk_scores[risk_scores["hcp_id"] == hcp_id].empty:
        raise HTTPException(status_code=404, detail=f"HCP '{hcp_id}' not found")

    row = event_features[event_features["hcp_id"] == hcp_id]
    if row.empty:
        return {"hcp_id": hcp_id, "has_speaker_events": False}

    record = row.iloc[0].replace({float("nan"): None}).to_dict()
    record["has_speaker_events"] = True
    return record
