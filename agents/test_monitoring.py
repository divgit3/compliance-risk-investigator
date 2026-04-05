"""
agents/test_monitoring.py — Manual smoke test for MonitoringAgent (Task 3.2)

Runs three monitoring scenarios and prints the full MonitoringReport JSON for
each. Not a pytest suite — run directly:

    source venv/bin/activate
    export OPENAI_API_KEY=sk-...
    python agents/test_monitoring.py

Requirements:
    - OPENAI_API_KEY set in environment
    - MLflow running at localhost:5001 (optional — logging failures are swallowed)
    - Phase 2 parquet outputs in features/outputs/ and models/outputs/
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from agents.monitoring_agent import MonitoringAgent


def _select_test_scenarios() -> list[dict]:
    """
    Derive test scenarios dynamically from the Phase 2 parquets.

    Scenario 1: Full population (no filters)
    Scenario 2: State filter — state with the highest HCP count
    Scenario 3: State filter — state with the most critical-tier HCPs
    """
    risk = pd.read_parquet(_ROOT / "models/outputs/risk_scores.parquet")
    raw  = pd.read_parquet(_ROOT / "features/outputs/feature_store_raw.parquet")

    merged = risk.merge(raw[["hcp_id", "state"]], on="hcp_id", how="left")

    # State with most HCPs
    top_state_by_count = merged["state"].value_counts().idxmax()

    # State with most critical-tier HCPs
    crit_by_state = (
        merged[merged["risk_tier"] == "critical"]["state"].value_counts()
    )
    top_state_by_critical = crit_by_state.idxmax() if not crit_by_state.empty else top_state_by_count

    return [
        {
            "label":     "Scenario 1: Full population",
            "specialty": None,
            "state":     None,
            "risk_tier": None,
        },
        {
            "label":     f"Scenario 2: Top state by HCP count ({top_state_by_count})",
            "specialty": None,
            "state":     top_state_by_count,
            "risk_tier": None,
        },
        {
            "label":     f"Scenario 3: State with most critical HCPs ({top_state_by_critical})",
            "specialty": None,
            "state":     top_state_by_critical,
            "risk_tier": "critical",
        },
    ]


async def _run_scenarios(
    agent: MonitoringAgent,
    scenarios: list[dict],
) -> None:
    total_start = time.monotonic()

    for scenario in scenarios:
        print(f"\n{'='*64}")
        print(f"  {scenario['label']}")
        print(f"{'='*64}")
        t0 = time.monotonic()

        report = await agent.monitor(
            specialty=scenario["specialty"],
            state=scenario["state"],
            risk_tier=scenario["risk_tier"],
        )

        elapsed = time.monotonic() - t0

        print(report.model_dump_json(indent=2))

        # Summary line
        print(f"\n  ── Summary ──")
        print(f"  total_hcps_in_scope : {report.total_hcps_in_scope:,}")
        print(f"  critical_count      : {report.risk_distribution.critical_count}")
        print(f"  high_count          : {report.risk_distribution.high_count}")
        print(f"  num_systemic_issues : {len(report.systemic_issues)}")
        print(f"  elapsed             : {elapsed:.1f}s")

    total = time.monotonic() - total_start
    print(f"\n{'='*64}")
    print(f"  Total elapsed: {total:.1f}s for {len(scenarios)} scenarios")
    print(f"{'='*64}\n")


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in environment.")
        print("       export OPENAI_API_KEY=sk-...")
        sys.exit(1)

    print("── Selecting test scenarios ─────────────────────────────────")
    scenarios = _select_test_scenarios()
    for s in scenarios:
        print(f"  {s['label']}")

    print("\n── Initialising MonitoringAgent ─────────────────────────────")
    agent = MonitoringAgent(openai_api_key=api_key, model="gpt-4o-mini")
    print("  Agent ready.")

    asyncio.run(_run_scenarios(agent, scenarios))


if __name__ == "__main__":
    main()
