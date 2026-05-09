# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
api/main.py — FastAPI application entry point (Task 3.4)

Lifespan:
  - Loads risk_scores, rule_flags, event_feature_matrix parquets once
  - Reads OPENAI_API_KEY from environment
  - Agents initialize lazily on first request (see api/dependencies.py)

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
_DATA_DIR     = _ROOT / "data"    / "processed"

# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("STARTUP: loading parquets...", flush=True)
    set_parquet("risk_scores",    pd.read_parquet(_MODELS_DIR   / "risk_scores.parquet"))
    print("STARTUP: risk_scores loaded", flush=True)
    set_parquet("rule_flags",     pd.read_parquet(_MODELS_DIR   / "rule_flags.parquet"))
    print("STARTUP: rule_flags loaded", flush=True)
    set_parquet("event_features", pd.read_parquet(_FEATURES_DIR / "event_feature_matrix.parquet"))
    print("STARTUP: event_features loaded", flush=True)
    set_parquet("shap_values", pd.read_parquet(_MODELS_DIR / "shap_values.parquet"))
    print("STARTUP: shap_values loaded", flush=True)

    for _bm_name, _bm_path in [
        ("competitor_benchmarks", _FEATURES_DIR / "competitor_benchmarks.parquet"),
        ("population_benchmarks", _FEATURES_DIR / "population_benchmarks.parquet"),
    ]:
        if _bm_path.exists():
            set_parquet(_bm_name, pd.read_parquet(_bm_path))
        else:
            set_parquet(_bm_name, None)
    print("STARTUP: benchmarks loaded", flush=True)

    set_parquet("interactions", pd.read_parquet(_DATA_DIR / "hcp_interactions.parquet"))
    print("STARTUP: interactions loaded", flush=True)

    _tov_path = _DATA_DIR / "hcp_tov_summary.parquet"
    if _tov_path.exists():
        set_parquet("tov_summary", pd.read_parquet(_tov_path))
        print("STARTUP: tov_summary loaded", flush=True)
    else:
        set_parquet("tov_summary", None)
        print("STARTUP: tov_summary not found — CMS dollar benchmarks unavailable", flush=True)

    api_key = os.environ.get("OPENAI_API_KEY")
    print(f"STARTUP: api_key present={bool(api_key)}", flush=True)

    set_agent("api_key", api_key)
    set_agent("investigation", None)
    set_agent("monitoring", None)
    set_agent("policy", None)

    print("STARTUP: agents will initialize lazily on first request", flush=True)
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
