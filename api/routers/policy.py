# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
api/routers/policy.py — Policy Q&A endpoint

POST /policy/query — Ask a natural language compliance question
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_policy_agent

router = APIRouter(prefix="/policy", tags=["policy"])


class PolicyQueryRequest(BaseModel):
    question: str


@router.post("/query")
async def policy_query(
    body:  PolicyQueryRequest,
    agent = Depends(get_policy_agent),
):
    """
    Answer a natural language compliance question using the PolicyAgent.

    The agent searches the policy knowledge base (5 docs, 128 chunks) and
    the rules registry. Returns a PolicyAnswer with citations, thresholds,
    and Nova vs PhRMA comparisons.

    This endpoint is async and calls the LLM — expect ~5-15s latency.
    """
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail="Policy agent not available — OPENAI_API_KEY not set",
        )
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
        answer = await asyncio.wait_for(agent.query(body.question), timeout=90.0)
        return answer.model_dump()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Policy agent timed out after 90s — check Qdrant is running")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc
