"""
api/routers/monitoring.py — Population-level monitoring endpoint

GET /monitoring — Run MonitoringAgent over the full population (or scoped subset)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_monitoring_agent

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("")
async def get_monitoring_report(
    specialty: Optional[str] = Query(None, description="Filter by specialty"),
    state:     Optional[str] = Query(None, description="Filter by state"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    agent = Depends(get_monitoring_agent),
):
    """
    Run the MonitoringAgent to generate a population-level compliance report.

    Filters (specialty, state, risk_tier) are passed through to the agent.
    This endpoint is async and calls the LLM — expect ~10-30s latency.
    """
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail="Monitoring agent not available — OPENAI_API_KEY not set",
        )
    try:
        report = await agent.monitor(
            specialty=specialty,
            state=state,
            risk_tier=risk_tier,
        )
        return report.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc
