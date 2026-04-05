"""
api/main.py — FastAPI application entry point (Task 3.4)

Lifespan:
  - Loads risk_scores, rule_flags, event_feature_matrix parquets once
  - Initialises InvestigationAgent, MonitoringAgent, PolicyAgent once
  - Reads OPENAI_API_KEY from environment

Run:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.dependencies import set_agent, set_parquet
from api.routers import benchmarks, events, hcps, monitoring, policy

# ── Paths ──────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[1]
_FEATURES_DIR = _ROOT / "features" / "outputs"
_MODELS_DIR   = _ROOT / "models"  / "outputs"


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load parquets and init agents once at startup; clean up on shutdown."""

    # ── Parquets ──────────────────────────────────────────────────────────────
    set_parquet("risk_scores",    pd.read_parquet(_MODELS_DIR   / "risk_scores.parquet"))
    set_parquet("rule_flags",     pd.read_parquet(_MODELS_DIR   / "rule_flags.parquet"))
    set_parquet("event_features", pd.read_parquet(_FEATURES_DIR / "event_feature_matrix.parquet"))

    # ── Agents ────────────────────────────────────────────────────────────────
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        from agents.investigation_agent import InvestigationAgent
        from agents.monitoring_agent    import MonitoringAgent
        from agents.policy_agent        import PolicyAgent

        set_agent("investigation", InvestigationAgent(openai_api_key=api_key))
        set_agent("monitoring",    MonitoringAgent(openai_api_key=api_key))
        set_agent("policy",        PolicyAgent(openai_api_key=api_key))
    else:
        # Allow the app to start without agents (returns 503 on agent endpoints)
        set_agent("investigation", None)
        set_agent("monitoring",    None)
        set_agent("policy",        None)

    yield

    # Nothing to clean up — parquets and agents are in-memory only


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Compliance Risk Investigator API",
    description=(
        "REST API for Nova Pharma HCP compliance risk data, "
        "investigation reports, monitoring analysis, and policy Q&A."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(hcps.router)
app.include_router(events.router)
app.include_router(monitoring.router)
app.include_router(policy.router)
app.include_router(benchmarks.router)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
