# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
agents/test_policy.py — Manual smoke test for PolicyAgent (Task 3.3)

Runs 6 compliance questions covering threshold lookups, rule interpretation,
scoring, OIG enforcement, and edge cases. Not a pytest suite — run directly:

    source venv/bin/activate
    export OPENAI_API_KEY=sk-...
    python agents/test_policy.py

Requirements:
    - OPENAI_API_KEY set in environment
    - Qdrant running at localhost:6333
    - MLflow running at localhost:5001 (optional — logging failures are swallowed)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from agents.policy_agent import PolicyAgent

QUESTIONS = [
    # 1. Threshold — meal limits
    "What is the meal expense limit for Nova Pharma speaker events?",

    # 2. Threshold + comparison — FMV cap
    (
        "What is the FMV cap for speaker engagements and how does it "
        "compare to PhRMA guidelines?"
    ),

    # 3. Rule interpretation — vague rationale
    (
        "What constitutes a vague rationale for an HCP interaction, "
        "and what are the compliance consequences?"
    ),

    # 4. Scoring / model — risk tiers
    (
        "How is an HCP's compliance risk score calculated and what "
        "triggers a critical risk tier?"
    ),

    # 5. OIG enforcement — speaker fraud
    (
        "What are the OIG's main concerns about speaker programs, "
        "and what patterns indicate potential fraud?"
    ),

    # 6. Edge case — possibly outside knowledge base
    (
        "What is the maximum number of speaker events an HCP can "
        "attend per year under Nova Pharma policy?"
    ),
]


async def _run_questions(agent: PolicyAgent, questions: list[str]) -> None:
    total_start = time.monotonic()

    for i, question in enumerate(questions, 1):
        print(f"\n{'='*64}")
        print(f"  Q{i}: {question[:80]}{'...' if len(question) > 80 else ''}")
        print(f"{'='*64}")

        t0 = time.monotonic()
        answer = await agent.query(question)
        elapsed = time.monotonic() - t0

        # Key metrics summary
        print(f"\n  confidence          : {answer.confidence}")
        print(f"  num_chunks          : {len(answer.relevant_chunks)}")
        print(f"  num_rules           : {len(answer.rule_thresholds)}")
        print(f"  num_nova_overrides  : {len(answer.nova_vs_phrma)}")
        print(f"  chunk_ids_for_audit : {answer.chunk_ids_for_audit}")

        # Nova vs PhRMA comparisons (if any)
        if answer.nova_vs_phrma:
            print("\n  Nova Pharma vs PhRMA:")
            for nvp in answer.nova_vs_phrma:
                print(f"    {nvp.rule_name}: Nova={nvp.nova_threshold} | PhRMA={nvp.phrma_threshold}")

        # Rule thresholds matched
        if answer.rule_thresholds:
            print("\n  Rules matched:")
            for r in answer.rule_thresholds:
                print(f"    [{r['rule_id']}] {r['rule_name']}: {r['threshold']} ({r['authority']})")

        # Answer excerpt
        print(f"\n  Answer (first 400 chars):")
        print(f"  {answer.answer[:400]}")

        # Data limitations
        if answer.data_limitations:
            print(f"\n  Data limitations:")
            for lim in answer.data_limitations:
                print(f"    - {lim}")

        print(f"\n  elapsed: {elapsed:.1f}s")

    total = time.monotonic() - total_start
    print(f"\n{'='*64}")
    print(f"  Total elapsed: {total:.1f}s for {len(questions)} questions")
    print(f"{'='*64}\n")


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in environment.")
        print("       export OPENAI_API_KEY=sk-...")
        sys.exit(1)

    print("── Initialising PolicyAgent ─────────────────────────────────")
    agent = PolicyAgent(openai_api_key=api_key, model="gpt-4o-mini")
    print("  Agent ready.")
    print(f"  Running {len(QUESTIONS)} questions...\n")

    asyncio.run(_run_questions(agent, QUESTIONS))


if __name__ == "__main__":
    main()
