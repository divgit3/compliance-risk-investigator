"""
tests/test_dual_query_retrieval.py — Dual-query retrieval regression suite.

Validates the dual-query merge logic in search_policy_docs / _dual_search.

TEST 1  Single-query mode unchanged when ContextVar is not set.
TEST 2  Dual-query corrects the known DOC_002 inversion:
        agent query "annual meal limit HCP" inverts chunk_0000 / chunk_0001;
        raw question restores correct order.
TEST 3  Dual-query does not invert a query that was already correct:
        "annual speaker fee cap" → chunk_0001 stays #1.
TEST 4  Merge-by-max ensures both query winners appear in top-K:
        chunk A wins agent query, chunk B wins raw query → both in top-2.

All tests require a real OpenAI API key and Qdrant on port 6335.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ── QDRANT_PORT must be set before policy_tools is imported (module-level constant) ──
os.environ.setdefault("QDRANT_PORT", "6335")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from agents.tools.policy_tools import _dual_search, _raw_question_ctx, search_policy_docs  # noqa: E402

# ── Skip mark ─────────────────────────────────────────────────────────────────

_NEEDS_OPENAI = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY")
    or os.getenv("OPENAI_API_KEY", "").startswith("sk-test"),
    reason="Real OpenAI API key required for embedding tests",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _invoke(query: str, top_k: int = 3) -> dict:
    """Call search_policy_docs via its LangChain .invoke() interface."""
    raw = search_policy_docs.invoke({"query": query, "top_k": top_k})
    return json.loads(raw) if isinstance(raw, str) else raw


def _chunk_ids(result: dict) -> list[str]:
    return [r["chunk_id"] for r in result.get("results", [])]


def _score_for(result: dict, chunk_id: str) -> float | None:
    for r in result.get("results", []):
        if r["chunk_id"] == chunk_id:
            return r["relevance_score"]
    return None


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — Single-query mode: ContextVar absent → behaviour identical to before
# ══════════════════════════════════════════════════════════════════════════════

@_NEEDS_OPENAI
def test_single_query_mode_contextvar_absent():
    """
    TEST 1: When _raw_question_ctx is not set (default=None), search_policy_docs
    falls through to the single-query path and returns the known-inverted ranking
    for the diagnostic query. This is the regression baseline — the dual-query fix
    must not silently activate in single-query mode.
    """
    assert _raw_question_ctx.get() is None, "ContextVar should default to None"

    result = _invoke("annual meal limit HCP", top_k=3)
    assert "error" not in result, f"search_policy_docs returned error: {result}"
    assert result.get("results"), "Expected at least one result"

    ids = _chunk_ids(result)
    # Confirmed from diagnostic: single-query inverts the ranking
    assert ids[0] == "DOC_002_chunk_0001", (
        f"TEST 1 FAIL: single-query should produce the known inversion "
        f"(chunk_0001 first). Got: {ids}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — Dual-query corrects the known inversion
# ══════════════════════════════════════════════════════════════════════════════

@_NEEDS_OPENAI
def test_dual_query_corrects_inversion():
    """
    TEST 2: With the raw question set in the ContextVar, search_policy_docs enters
    dual-query mode and DOC_002_chunk_0000 (actual meal cap) ranks #1.

    Diagnostic baseline:
      single-query "annual meal limit HCP":   chunk_0001=0.4554, chunk_0000=0.4364 (inverted)
      raw question (full sentence):           chunk_0000=0.6881, chunk_0001=0.6864 (correct)
      merged by max:                          chunk_0000=0.6881, chunk_0001=0.6864 → correct
    """
    raw_q = "What is the annual meal cap per HCP at Nova Pharma?"
    token = _raw_question_ctx.set(raw_q)
    try:
        result = _invoke("annual meal limit HCP", top_k=3)
    finally:
        _raw_question_ctx.reset(token)

    assert "error" not in result, f"search_policy_docs returned error: {result}"
    ids = _chunk_ids(result)
    assert ids[0] == "DOC_002_chunk_0000", (
        f"TEST 2 FAIL: dual-query should rank chunk_0000 first. Got: {ids}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Dual-query does not invert a query that was already correct
# ══════════════════════════════════════════════════════════════════════════════

@_NEEDS_OPENAI
def test_dual_query_preserves_already_correct_ranking():
    """
    TEST 3: For a question about speaker fees, chunk_0001 (Section 3.2 Annual
    Speaker Fee Cap) should rank #1 both before and after the dual-query fix.
    Dual-query must not introduce a new inversion.
    """
    raw_q = "What is the annual speaker fee cap at Nova Pharma?"
    token = _raw_question_ctx.set(raw_q)
    try:
        result = _invoke("annual speaker fee cap Nova Pharma", top_k=3)
    finally:
        _raw_question_ctx.reset(token)

    assert "error" not in result, f"search_policy_docs returned error: {result}"
    ids = _chunk_ids(result)
    assert ids[0] == "DOC_002_chunk_0001", (
        f"TEST 3 FAIL: chunk_0001 (speaker fee cap) should rank #1. Got: {ids}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — Merge-by-max: both query winners appear in merged top-K
# ══════════════════════════════════════════════════════════════════════════════

@_NEEDS_OPENAI
def test_merge_max_both_winners_in_top_k():
    """
    TEST 4: When agent query favours chunk_0001 and raw question favours
    chunk_0000, BOTH appear in the merged top-2 and their scores reflect
    the max across both searches (not just the agent query score).

    Agent query "annual speaker fee cap HCP" → chunk_0001 wins
    Raw question "What is the annual meal cap per HCP at Nova Pharma?" → chunk_0000 wins
    Merged top-2 must contain both, each at its max score (from raw question,
    which scored both ~0.69 — above the agent query scores of ~0.45).
    """
    raw_q = "What is the annual meal cap per HCP at Nova Pharma?"
    token = _raw_question_ctx.set(raw_q)
    try:
        result = _invoke("annual speaker fee cap HCP", top_k=2)
    finally:
        _raw_question_ctx.reset(token)

    assert "error" not in result, f"search_policy_docs returned error: {result}"
    ids = _chunk_ids(result)

    assert "DOC_002_chunk_0000" in ids, (
        f"TEST 4 FAIL: chunk_0000 (meal cap) missing from top-2. Got: {ids}\n"
        "Oversampling at top_k*2 should have captured it from the raw-question search."
    )
    assert "DOC_002_chunk_0001" in ids, (
        f"TEST 4 FAIL: chunk_0001 (speaker fee) missing from top-2. Got: {ids}"
    )

    # Both scores should be elevated to the raw-question level (~0.69), not left
    # at the lower agent-query level (~0.45). Threshold: > 0.55 (midpoint).
    s0 = _score_for(result, "DOC_002_chunk_0000")
    s1 = _score_for(result, "DOC_002_chunk_0001")
    assert s0 is not None and s0 > 0.55, (
        f"TEST 4 FAIL: chunk_0000 score {s0} not elevated by raw-question search. "
        "Expected > 0.55 (raw question should have scored it ~0.69)."
    )
    assert s1 is not None and s1 > 0.55, (
        f"TEST 4 FAIL: chunk_0001 score {s1} not elevated by raw-question search. "
        "Expected > 0.55 (raw question should have scored it ~0.69)."
    )
