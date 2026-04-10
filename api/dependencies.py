"""
api/dependencies.py — Shared state and FastAPI dependency providers.

Parquets and agents are loaded ONCE at startup via the lifespan context
manager in api/main.py and stored in the module-level _STATE dict.
All routers import get_state() or the typed get_*() helpers below.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# ── Module-level state store ───────────────────────────────────────────────────

_STATE: dict[str, Any] = {}


# ── Setters (called from lifespan) ────────────────────────────────────────────

def set_parquet(name: str, df: pd.DataFrame) -> None:
    _STATE[f"parquet_{name}"] = df


def set_agent(name: str, agent: Any) -> None:
    _STATE[f"agent_{name}"] = agent


# ── FastAPI dependency helpers ─────────────────────────────────────────────────

def get_risk_scores() -> pd.DataFrame:
    return _STATE["parquet_risk_scores"]


def get_rule_flags() -> pd.DataFrame:
    return _STATE["parquet_rule_flags"]


def get_event_features() -> pd.DataFrame:
    return _STATE["parquet_event_features"]


def get_competitor_benchmarks() -> pd.DataFrame | None:
    return _STATE.get("parquet_competitor_benchmarks")


def get_population_benchmarks() -> pd.DataFrame | None:
    return _STATE.get("parquet_population_benchmarks")


def get_tov_summary() -> pd.DataFrame | None:
    return _STATE.get("parquet_tov_summary")


async def get_investigation_agent():
    if _STATE.get("agent_investigation") is None:
        api_key = _STATE.get("agent_api_key")
        if not api_key:
            return None
        from agents.investigation_agent import InvestigationAgent
        _STATE["agent_investigation"] = InvestigationAgent(openai_api_key=api_key)
    return _STATE["agent_investigation"]


async def get_monitoring_agent():
    if _STATE.get("agent_monitoring") is None:
        api_key = _STATE.get("agent_api_key")
        if not api_key:
            return None
        from agents.monitoring_agent import MonitoringAgent
        _STATE["agent_monitoring"] = MonitoringAgent(openai_api_key=api_key)
    return _STATE["agent_monitoring"]


async def get_policy_agent():
    if _STATE.get("agent_policy") is None:
        api_key = _STATE.get("agent_api_key")
        if not api_key:
            return None
        from agents.policy_agent import PolicyAgent
        _STATE["agent_policy"] = PolicyAgent(openai_api_key=api_key)
    return _STATE["agent_policy"]