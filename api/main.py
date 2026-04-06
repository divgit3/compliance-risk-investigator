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
import asyncio
import threading
# ── Paths ──────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[1]
_FEATURES_DIR = _ROOT / "features" / "outputs"
_MODELS_DIR   = _ROOT / "models"  / "outputs"

def _init_agents_background(api_key: str):
    """Initialize all agents in a background thread at startup."""
    from agents.investigation_agent import InvestigationAgent
    from agents.monitoring_agent import MonitoringAgent  
    from agents.policy_agent import PolicyAgent
    
    set_agent("investigation", InvestigationAgent(openai_api_key=api_key))
    set_agent("monitoring", MonitoringAgent(openai_api_key=api_key))
    set_agent("policy", PolicyAgent(openai_api_key=api_key))
    print("BACKGROUND: all agents ready", flush=True)

# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    import sys
    print("STARTUP: loading parquets...", flush=True)
    set_parquet("risk_scores",    pd.read_parquet(_MODELS_DIR   / "risk_scores.parquet"))
    print("STARTUP: risk_scores loaded", flush=True)
    set_parquet("rule_flags",     pd.read_parquet(_MODELS_DIR   / "rule_flags.parquet"))
    print("STARTUP: rule_flags loaded", flush=True)
    set_parquet("event_features", pd.read_parquet(_FEATURES_DIR / "event_feature_matrix.parquet"))
    print("STARTUP: event_features loaded", flush=True)

    for _bm_name, _bm_path in [
        ("competitor_benchmarks", _FEATURES_DIR / "competitor_benchmarks.parquet"),
        ("population_benchmarks", _FEATURES_DIR / "population_benchmarks.parquet"),
    ]:
        if _bm_path.exists():
            set_parquet(_bm_name, pd.read_parquet(_bm_path))
        else:
            set_parquet(_bm_name, None)
    print("STARTUP: benchmarks loaded", flush=True)

    api_key = os.environ.get("OPENAI_API_KEY")
    print(f"STARTUP: api_key present={bool(api_key)}", flush=True)
    
    # Store api_key for lazy agent initialization on first request
    # Agents are NOT initialized at startup — create_openai_tools_agent
    # makes a network call to OpenAI which hangs in Docker
    
    set_agent("api_key", api_key)
    set_agent("investigation", None)
    set_agent("monitoring", None)
    set_agent("policy", None)

    if api_key:
        t = threading.Thread(target=_init_agents_background, args=(api_key,), daemon=True)
        t.start()
        print("STARTUP: agent initialization started in background thread", flush=True)

    print("STARTUP: complete", flush=True)
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
