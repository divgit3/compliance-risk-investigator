"""
agents/test_investigation.py — Manual smoke test for InvestigationAgent (Task 3.1)

Runs three investigations (critical, high, low tier HCPs) and prints the full
InvestigationReport JSON for each. Not a pytest suite — run directly:

    source venv/bin/activate
    export OPENAI_API_KEY=sk-...
    python agents/test_investigation.py

Requirements:
    - OPENAI_API_KEY set in environment
    - Qdrant running at localhost:6333
    - MLflow running at localhost:5001
    - Phase 2 parquet outputs in features/outputs/ and models/outputs/
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pandas as pd

# Allow running from project root or agents/ directory
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from agents.investigation_agent import InvestigationAgent


def _select_test_hcps() -> tuple[str, str, str]:
    """Select one HCP per tier (critical, high, low) from risk_scores.parquet."""
    risk = pd.read_parquet(_ROOT / "models/outputs/risk_scores.parquet")

    crit_rows = risk[risk["risk_tier"] == "critical"]
    high_rows = risk[risk["risk_tier"] == "high"]
    low_rows  = risk[risk["risk_tier"] == "low"]

    if crit_rows.empty:
        raise RuntimeError("No critical-tier HCPs found in risk_scores.parquet")

    hcp_critical = crit_rows.sort_values("risk_score", ascending=False).iloc[0]["hcp_id"]
    hcp_high     = high_rows.sort_values("risk_score", ascending=False).iloc[0]["hcp_id"]
    hcp_low      = low_rows.sort_values("risk_score").iloc[0]["hcp_id"]

    return str(hcp_critical), str(hcp_high), str(hcp_low)


async def _run_investigations(
    agent: InvestigationAgent,
    hcp_ids: list[str],
) -> None:
    total_start = time.monotonic()

    for hcp_id in hcp_ids:
        print(f"\n{'='*64}")
        print(f"  Investigating: {hcp_id}")
        print(f"{'='*64}")
        t0 = time.monotonic()
        report = await agent.investigate(hcp_id)
        elapsed = time.monotonic() - t0

        print(report.model_dump_json(indent=2))
        print(f"\n  ── Elapsed: {elapsed:.1f}s ──\n")

    total = time.monotonic() - total_start
    print(f"\n{'='*64}")
    print(f"  Total elapsed: {total:.1f}s for {len(hcp_ids)} investigations")
    print(f"{'='*64}\n")


def main() -> None:
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
        sys.exit(1)

    print("── Selecting test HCPs ──────────────────────────────────────")
    hcp_critical, hcp_high, hcp_low = _select_test_hcps()
    print(f"  Critical tier: {hcp_critical}")
    print(f"  High tier:     {hcp_high}")
    print(f"  Low tier:      {hcp_low}")

    print("\n── Initialising InvestigationAgent ──────────────────────────")
    agent = InvestigationAgent(openai_api_key=OPENAI_API_KEY, model="gpt-4o-mini")
    print("  Agent ready.")

    asyncio.run(_run_investigations(agent, [hcp_critical, hcp_high, hcp_low]))


if __name__ == "__main__":
    main()
