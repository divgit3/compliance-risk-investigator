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


def get_investigation_agent():
    return _STATE["agent_investigation"]


def get_monitoring_agent():
    return _STATE["agent_monitoring"]


def get_policy_agent():
    return _STATE["agent_policy"]
