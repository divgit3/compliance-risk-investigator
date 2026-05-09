# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
conftest.py — pytest configuration for compliance-risk-investigator

Adds the project root to sys.path so that imports from models/, features/,
pipelines/, and compliance/ work without package installation.

Also provides the async HTTPX client fixture for API tests (Task 3.8).

Note on lifespan: httpx ASGITransport does NOT fire ASGI lifespan events.
The fixture initialises the app _STATE directly (mirroring api/main.py lifespan)
so all parquets are available during tests without a live server.

Agent tests (@pytest.mark.agent) require OPENAI_API_KEY to be a real key.
Non-agent tests use a dummy key — agents are set to None, never invoked.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root so cross-module imports resolve correctly during test runs.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set dummy key BEFORE importing the FastAPI app so agents init without error.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")

import pandas as pd              # noqa: E402
import pytest_asyncio            # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from api.dependencies import set_agent, set_parquet  # noqa: E402
from api.main import app  # noqa: E402

_ROOT        = Path(__file__).resolve().parents[1]
_FEATURES    = _ROOT / "features" / "outputs"
_MODELS      = _ROOT / "models"   / "outputs"


def _init_app_state() -> None:
    """
    Populate api.dependencies._STATE with all parquets and dummy agents.

    This mirrors api/main.py lifespan exactly. Called once per test session
    because ASGITransport does not fire ASGI lifespan startup events.
    """
    set_parquet("risk_scores",    pd.read_parquet(_MODELS   / "risk_scores.parquet"))
    set_parquet("rule_flags",     pd.read_parquet(_MODELS   / "rule_flags.parquet"))
    set_parquet("event_features", pd.read_parquet(_FEATURES / "event_feature_matrix.parquet"))

    for _name, _path in [
        ("competitor_benchmarks", _FEATURES / "competitor_benchmarks.parquet"),
        ("population_benchmarks", _FEATURES / "population_benchmarks.parquet"),
    ]:
        set_parquet(_name, pd.read_parquet(_path) if _path.exists() else None)

    # Agents: initialise real agents only when a genuine API key is present.
    # Non-agent tests never call agent endpoints, so None is safe.
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key and not api_key.startswith("sk-test"):
        try:
            from agents.investigation_agent import InvestigationAgent
            from agents.monitoring_agent    import MonitoringAgent
            from agents.policy_agent        import PolicyAgent
            set_agent("investigation", InvestigationAgent(openai_api_key=api_key))
            set_agent("monitoring",    MonitoringAgent(openai_api_key=api_key))
            set_agent("policy",        PolicyAgent(openai_api_key=api_key))
        except ImportError:
            # LangChain version mismatch — agent tests will fail but non-agent
            # tests (including sentence-highlighting tests) are unaffected.
            set_agent("investigation", None)
            set_agent("monitoring",    None)
            set_agent("policy",        None)
    else:
        set_agent("investigation", None)
        set_agent("monitoring",    None)
        set_agent("policy",        None)


# Initialise state once at import time (session-level, before any fixture runs)
_init_app_state()


@pytest_asyncio.fixture(scope="module")
async def async_client():
    """Module-scoped async HTTPX client backed by the FastAPI ASGI app.

    State is already loaded by _init_app_state() above.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
