"""
evaluation/policy_ragas/run_evaluation.py — Policy Agent RAGAS test harness.

Stage 1: verify plumbing (single hardcoded question, no metrics).
Stage 2: golden dataset populated — use it for baseline measurement.
Stage 3: RAGAS metrics against full dataset with per-category analysis.

Run:
    set -a && source docker/.env && set +a
    python -m evaluation.policy_ragas.run_evaluation
    python evaluation/policy_ragas/run_evaluation.py
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

API_BASE     = "http://localhost:8000"
TIMEOUT      = 120  # seconds — policy agent is 5–15s; buffer for cold starts

_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"
_RESULTS_DIR  = Path(__file__).parent / "results"
_PREV_BASELINE = _RESULTS_DIR / "20260430T191343Z" / "summary.json"  # 1.2g pass 1 flag disambiguation

STAGE1_QUESTION = "What is the annual HCP spend cap for Nova Pharma?"

# CI gates — applied ONLY to the rule_backed category
CI_GATES = {
    "faithfulness":      0.7,
    "answer_relevancy":  0.7,
    "context_precision": 0.5,
    "latency_p95_ms":    15000,
}


# ── Agent API ─────────────────────────────────────────────────────────────────

def query_policy_agent(question: str) -> tuple[dict, float]:
    """POST /policy/query, return (response_dict, latency_ms)."""
    t0 = time.monotonic()
    resp = requests.post(
        f"{API_BASE}/policy/query",
        json={"question": question},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    latency_ms = (time.monotonic() - t0) * 1000
    return resp.json(), latency_ms


# ── RAGAS helpers ─────────────────────────────────────────────────────────────

def build_ragas_sample(entry: dict, agent_response: dict):
    """Convert a golden dataset entry + agent response into a RAGAS SingleTurnSample.

    retrieved_contexts includes BOTH Qdrant excerpt chunks AND serialized
    lookup_rule results (rule_thresholds + nova_vs_phrma). This makes both
    agent information sources visible to RAGAS Faithfulness so claims
    derived from the rules registry don't score 0.

    All contexts land in a single undifferentiated pool; source-marker
    prefixes ([qdrant:], [rules_registry], [nova_vs_phrma]) let the RAGAS
    LLM distinguish provenance without requiring separate pools.
    """
    from ragas.dataset_schema import SingleTurnSample

    contexts: list[str] = []

    # Source 1: Qdrant semantic-search chunks
    for c in (agent_response.get("relevant_chunks") or []):
        excerpt = (c.get("excerpt") or "").strip()
        if excerpt:
            source = c.get("source_doc") or "unknown"
            contexts.append(f"[qdrant:{source}] {excerpt}")

    # Source 2: lookup_rule results — each rule is a standalone context chunk
    for r in (agent_response.get("rule_thresholds") or []):
        rid = r.get("rule_id") or ""
        if not rid:
            continue
        name      = r.get("rule_name", "")
        threshold = r.get("threshold", "")
        authority = r.get("authority", "")
        contexts.append(
            f"[rules_registry] {rid}: {name} — threshold: {threshold} "
            f"(authority: {authority})"
        )

    # Source 3: Nova vs PhRMA comparison data (carries phrma_threshold values
    # the agent cites in comparative answers — not in rule_thresholds above)
    for c in (agent_response.get("nova_vs_phrma") or []):
        src_id  = c.get("source_rule_id") or ""
        if not src_id:
            continue
        name    = c.get("rule_name", "")
        nova_t  = c.get("nova_threshold", "")
        phrma_t = c.get("phrma_threshold") or "N/A"
        contexts.append(
            f"[nova_vs_phrma] {src_id} ({name}): Nova threshold={nova_t}, "
            f"PhRMA equivalent={phrma_t}, Nova is stricter=True"
        )

    if not contexts:
        contexts = ["[No policy chunks retrieved and no rules matched]"]

    return SingleTurnSample(
        user_input=entry["user_input"],
        response=agent_response.get("answer", ""),
        retrieved_contexts=contexts,
        reference=entry["reference"],
    )


# ── Diagnostic extraction ─────────────────────────────────────────────────────

def extract_diagnostics(agent_response: dict, latency_ms: float) -> dict:
    """Extract agent-side diagnostic fields for cross-comparison analysis."""
    chunks       = agent_response.get("relevant_chunks") or []
    rule_thresh  = agent_response.get("rule_thresholds") or []
    limitations  = agent_response.get("data_limitations") or []
    gc           = agent_response.get("groundedness_check")

    relevance_scores = [c.get("relevance_score", 0.0) for c in chunks]

    # Scope-mismatch safety net: _detect_scope_mismatch and
    # _detect_unsupported_scope_dimension in policy_agent.py.
    scope_mismatch_detected = any(
        "safety net" in lim.lower()
        or ("scope" in lim.lower() and "retrieved" in lim.lower())
        for lim in limitations
    )
    # Over-narration post-processor: over_narration.py strip_over_narration().
    over_narration_stripped = any(
        "answer trimmed" in lim.lower() or "over-narration" in lim.lower()
        for lim in limitations
    )
    safety_net_fired = scope_mismatch_detected or over_narration_stripped

    return {
        "confidence":               agent_response.get("confidence", ""),
        "latency_ms":               round(latency_ms, 1),
        "chunk_count":              len(chunks),
        "relevance_scores":         [round(s, 4) for s in relevance_scores],
        "avg_relevance":            round(
            sum(relevance_scores) / len(relevance_scores), 4
        ) if relevance_scores else 0.0,
        "rule_ids_matched":         [
            r.get("rule_id") for r in rule_thresh if r.get("rule_id")
        ],
        "nova_vs_phrma_count":      len(agent_response.get("nova_vs_phrma") or []),
        "chunk_ids_for_audit":      agent_response.get("chunk_ids_for_audit") or [],
        "data_limitations_count":   len(limitations),
        "data_limitations":         limitations,
        "scope_mismatch_detected":  scope_mismatch_detected,
        "over_narration_stripped":  over_narration_stripped,
        "safety_net_fired":         safety_net_fired,
        "groundedness_check":       gc,
    }


# ── Statistics helpers ────────────────────────────────────────────────────────

def _is_finite(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and not math.isnan(v)


def _stat_block(values: list) -> dict:
    """Mean/median/min/max/count over a list; ignores None and NaN."""
    clean = [float(v) for v in values if _is_finite(v)]
    if not clean:
        return {"mean": None, "median": None, "min": None, "max": None, "count": 0}
    return {
        "mean":   round(statistics.mean(clean), 4),
        "median": round(statistics.median(clean), 4),
        "min":    round(min(clean), 4),
        "max":    round(max(clean), 4),
        "count":  len(clean),
    }


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Return pth percentile of a sorted list."""
    if not sorted_vals:
        return 0.0
    idx = int(math.ceil(p / 100.0 * len(sorted_vals))) - 1
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


# ── Summary builders ──────────────────────────────────────────────────────────

_METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def _category_block(entries: list[dict]) -> dict:
    out: dict[str, Any] = {}
    for m in _METRICS:
        vals = [e["ragas_scores"].get(m) for e in entries]
        out[m] = _stat_block(vals)

    latencies = [e["diagnostics"]["latency_ms"] for e in entries]
    out["latency_ms"] = _stat_block(latencies)
    out["latency_p95_ms"] = round(
        _percentile(sorted(latencies), 95), 1
    ) if latencies else None

    out["safety_net_fired_count"] = sum(
        1 for e in entries if e["diagnostics"]["safety_net_fired"]
    )
    out["scope_mismatch_count"] = sum(
        1 for e in entries if e["diagnostics"].get("scope_mismatch_detected")
    )
    out["over_narration_count"] = sum(
        1 for e in entries if e["diagnostics"].get("over_narration_stripped")
    )
    grounded_vals = [
        e["diagnostics"]["groundedness_check"]["grounded"]
        for e in entries
        if e["diagnostics"].get("groundedness_check") is not None
    ]
    out["groundedness_true_count"]   = sum(1 for v in grounded_vals if v)
    out["groundedness_total_checked"] = len(grounded_vals)
    out["count"] = len(entries)
    return out


def build_summary(results: list[dict]) -> dict:
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    return {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "total_entries": len(results),
        "categories":    {cat: _category_block(entries) for cat, entries in by_cat.items()},
        "aggregate":     _category_block(results),
    }


def build_notable_observations(results: list[dict]) -> list[dict]:
    notable = []
    for r in results:
        reasons = []
        gc = r["diagnostics"].get("groundedness_check")
        if gc is not None and not gc.get("grounded", True):
            reasons.append(
                f"groundedness_check.grounded=False — {gc.get('reasoning', '')}"
            )
        faith = r["ragas_scores"].get("faithfulness")
        if _is_finite(faith) and faith < 0.5:
            reasons.append(f"RAGAS Faithfulness={faith:.3f} < 0.5")
        if r["diagnostics"].get("safety_net_fired"):
            diag = r["diagnostics"]
            parts = []
            if diag.get("scope_mismatch_detected"):
                parts.append("scope mismatch")
            if diag.get("over_narration_stripped"):
                parts.append("over-narration stripped")
            what = " + ".join(parts) if parts else "safety net"
            reasons.append(f"safety net fired ({what})")
        if reasons:
            notable.append({
                "id":         r["id"],
                "category":   r["category"],
                "user_input": r["user_input"],
                "reasons":    reasons,
            })
    return notable


def build_summary_md(
    summary: dict,
    notable: list[dict],
    prev_summary: dict | None = None,
) -> str:
    metric_labels = {
        "faithfulness":      "Faithful.",
        "answer_relevancy":  "Ans.Rel.",
        "context_precision": "Ctx.Prec.",
        "context_recall":    "Ctx.Rec.",
    }

    lines = [
        "# Policy RAGAS Baseline",
        "",
        f"**Generated:** {summary['generated_at']}  ",
        f"**Total entries:** {summary['total_entries']}",
        "",
        "## Per-Category Metric Summary",
        "",
        "| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | "
        "Safety Net | Grounded |",
        "|----------|---|-----------|----------|-----------|----------"
        "|------------|----------|",
    ]

    category_order = [
        "rule_backed", "retrieval", "unanswerable", "false_premise", "registry_gap"
    ]
    cats = summary["categories"]

    def fmt(v) -> str:
        return f"{v:.3f}" if _is_finite(v) else "N/A"

    for cat in category_order:
        if cat not in cats:
            continue
        c = cats[cat]
        n  = c["count"]
        sn = c["safety_net_fired_count"]
        g  = c["groundedness_true_count"]
        gt = c["groundedness_total_checked"]
        vals = [fmt(c[m]["mean"]) for m in _METRICS]
        lines.append(
            f"| {cat} | {n} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} | "
            f"{sn}/{n} | {g}/{gt} |"
        )

    # Aggregate row
    agg  = summary["aggregate"]
    n    = agg["count"]
    sn   = agg["safety_net_fired_count"]
    g    = agg["groundedness_true_count"]
    gt   = agg["groundedness_total_checked"]
    vals = [fmt(agg[m]["mean"]) for m in _METRICS]
    lines.append(
        f"| **ALL** | **{n}** | **{vals[0]}** | **{vals[1]}** | "
        f"**{vals[2]}** | **{vals[3]}** | **{sn}/{n}** | **{g}/{gt}** |"
    )

    # Notable observations
    lines += ["", "## Notable Observations", ""]
    if notable:
        for obs in notable:
            lines.append(f"### `{obs['id']}` ({obs['category']})")
            lines.append(f"> {obs['user_input']}")
            for reason in obs["reasons"]:
                lines.append(f"- {reason}")
            lines.append("")
    else:
        lines += ["*No notable observations — all entries passed thresholds.*", ""]

    # CI gates table (rule_backed only)
    rb = cats.get("rule_backed", {})
    lines += ["## CI Gates (rule_backed category only)", ""]
    lines.append("| Gate | Threshold | rule_backed mean | Pass? |")
    lines.append("|------|-----------|------------------|-------|")
    for gate, threshold in CI_GATES.items():
        if gate == "latency_p95_ms":
            p95 = rb.get("latency_p95_ms")
            verdict = "✓" if (p95 is not None and p95 <= threshold) else "✗"
            v_str = f"{p95:.0f}ms" if p95 is not None else "N/A"
            lines.append(f"| {gate} | ≤{threshold}ms | {v_str} | {verdict} |")
        else:
            v = rb.get(gate, {}).get("mean")
            verdict = "✓" if (_is_finite(v) and v >= threshold) else "✗"
            v_str = fmt(v)
            lines.append(f"| {gate} | ≥{threshold} | {v_str} | {verdict} |")

    if prev_summary:
        lines += ["", build_delta_md(summary, prev_summary)]

    return "\n".join(lines) + "\n"


# ── Delta comparison ──────────────────────────────────────────────────────────

def build_delta_md(current: dict, prev: dict) -> str:
    """Build a markdown section showing per-category metric deltas vs a previous run."""

    def _mean(summary: dict, cat: str, metric: str):
        return (summary.get("categories", {})
                       .get(cat, {})
                       .get(metric, {})
                       .get("mean"))

    def _delta_str(cur_v, prev_v) -> str:
        if not _is_finite(cur_v) and not _is_finite(prev_v):
            return "N/A → N/A"
        if not _is_finite(prev_v):
            return f"N/A → {cur_v:.3f}"
        if not _is_finite(cur_v):
            return f"{prev_v:.3f} → N/A"
        delta = cur_v - prev_v
        sign  = "+" if delta >= 0 else ""
        return f"{prev_v:.3f} → {cur_v:.3f} ({sign}{delta:.3f})"

    prev_ts = prev.get("generated_at", "previous run")
    cur_ts  = current.get("generated_at", "current run")
    lines = [
        "## Delta from Previous Baseline",
        "",
        f"**Previous:** {prev_ts}  ",
        f"**Current:**  {cur_ts}",
        "",
        "| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |",
        "|----------|-------------|----------|-----------|----------|",
    ]

    category_order = [
        "rule_backed", "retrieval", "unanswerable", "false_premise", "registry_gap", "aggregate"
    ]
    for cat in category_order:
        if cat == "aggregate":
            cur_block  = current.get("aggregate", {})
            prev_block = prev.get("aggregate", {})
            label = "**ALL**"
        else:
            cur_block  = current.get("categories", {}).get(cat, {})
            prev_block = prev.get("categories", {}).get(cat, {})
            label = cat
            if not cur_block and not prev_block:
                continue

        row = [label]
        for m in _METRICS:
            cv = cur_block.get(m, {}).get("mean")
            pv = prev_block.get(m, {}).get("mean")
            row.append(_delta_str(cv, pv))
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines) + "\n"


# ── CI gate check ─────────────────────────────────────────────────────────────

def run_ci_gates(summary: dict) -> list[str]:
    """Return list of CI gate failure messages (rule_backed only)."""
    failures = []
    rb = summary["categories"].get("rule_backed", {})
    for gate, threshold in CI_GATES.items():
        if gate == "latency_p95_ms":
            p95 = rb.get("latency_p95_ms")
            if p95 is not None and p95 > threshold:
                failures.append(
                    f"CI gate FAILED [{gate}]: rule_backed P95={p95:.0f}ms > {threshold}ms"
                )
        else:
            v = rb.get(gate, {}).get("mean")
            if _is_finite(v) and v < threshold:
                failures.append(
                    f"CI gate FAILED [{gate}]: rule_backed mean={v:.3f} < {threshold}"
                )
    return failures


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    dataset_raw = json.loads(_DATASET_PATH.read_text())

    # Stage 1 smoke path — still works when dataset is empty
    if not dataset_raw:
        print(f"golden_dataset.json is empty — running Stage 1 smoke test.\n")
        print(f"Question: {STAGE1_QUESTION}\n")
        result, _ = query_policy_agent(STAGE1_QUESTION)
        print(json.dumps(result, indent=2, default=str))
        citations = result.get("relevant_chunks", [])
        print(f"\n--- {len(citations)} citation(s) returned ---")
        if citations:
            print("Stage 1 PASSED: policy agent returned a response with citations.")
        else:
            print("WARNING: response contained no citations — check Qdrant health.")
        return

    # Stage 2/3 — full evaluation
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            "ERROR: OPENAI_API_KEY not set.\n"
            "Run:  set -a && source docker/.env && set +a"
        )
        sys.exit(1)

    print(f"Loaded {len(dataset_raw)} entries from golden_dataset.json")
    categories = sorted({e["category"] for e in dataset_raw})
    print(f"Categories: {categories}")
    print(f"Calling agent at {API_BASE} ...\n")

    # ── Step 1: Collect agent responses ───────────────────────────────────────

    ragas_samples = []
    per_entry_data = []

    for i, entry in enumerate(dataset_raw, 1):
        qid      = entry["id"]
        question = entry["user_input"]
        print(f"  [{i:02d}/{len(dataset_raw)}] {qid}")
        print(f"    Q: {question[:80]}...")

        try:
            agent_response, latency_ms = query_policy_agent(question)
            error = None
        except Exception as e:
            print(f"    ERROR: {e}")
            agent_response = {
                "answer":           f"[agent error: {e}]",
                "relevant_chunks":  [],
                "rule_thresholds":  [],
                "nova_vs_phrma":    [],
                "confidence":       "low",
                "data_limitations": [f"Agent error: {e}"],
                "groundedness_check": None,
                "chunk_ids_for_audit": [],
            }
            latency_ms = 0.0
            error = str(e)

        conf     = agent_response.get("confidence", "?")
        n_chunks = len(agent_response.get("relevant_chunks") or [])
        print(f"    → confidence={conf}, chunks={n_chunks}, latency={latency_ms:.0f}ms")

        sample = build_ragas_sample(entry, agent_response)
        ragas_samples.append(sample)
        per_entry_data.append({
            "entry":           entry,
            "answer":          agent_response.get("answer", ""),
            "agent_reasoning": agent_response.get("agent_reasoning", ""),
            "chunks":          agent_response.get("relevant_chunks") or [],
            "rule_thresholds": agent_response.get("rule_thresholds") or [],
            "nova_vs_phrma":   agent_response.get("nova_vs_phrma") or [],
            "ragas_contexts":  sample.retrieved_contexts,  # enriched dual-source list
            "latency_ms":      latency_ms,
            "error":           error,
            "diagnostics":     extract_diagnostics(agent_response, latency_ms),
        })

    # ── Step 2: RAGAS metrics ─────────────────────────────────────────────────

    print(f"\nRunning RAGAS metrics on {len(ragas_samples)} samples...")

    import warnings
    from openai import OpenAI as _OpenAI
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset
    from ragas.llms import llm_factory

    # ragas.metrics exports module-level Metric instances (deprecated but functional).
    # ragas.metrics.collections exports ABCMeta classes, NOT Metric instances —
    # evaluate() rejects them. Use the deprecated instances; pass llm/embeddings
    # to evaluate() so it injects them automatically.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from ragas.metrics import (
            faithfulness       as _faithfulness,
            answer_relevancy   as _answer_relevancy,
            context_precision  as _context_precision,
            context_recall     as _context_recall,
        )

    from langchain_openai import OpenAIEmbeddings as LCOpenAIEmbeddings

    _client = _OpenAI(api_key=api_key)
    _llm    = llm_factory("gpt-4o-mini", client=_client, max_tokens=8192)
    # LangchainEmbeddings → evaluate() auto-wraps with LangchainEmbeddingsWrapper,
    # which exposes embed_query (required by answer_relevancy).
    # ragas.embeddings.OpenAIEmbeddings only has embed_text — causes AttributeError.
    _lc_emb = LCOpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key)

    ragas_result = evaluate(
        dataset=EvaluationDataset(samples=ragas_samples),
        metrics=[_faithfulness, _answer_relevancy, _context_precision, _context_recall],
        llm=_llm,
        embeddings=_lc_emb,
        raise_exceptions=False,
        show_progress=True,
    )

    scores_list = ragas_result.scores  # List[Dict[str, Any]], one per sample
    print(f"RAGAS complete. Building result records...")

    # ── Step 3: Merge into result records ─────────────────────────────────────

    all_results: list[dict] = []
    for data, scores in zip(per_entry_data, scores_list):
        entry = data["entry"]
        record: dict[str, Any] = {
            "id":                  entry["id"],
            "category":            entry["category"],
            "user_input":          entry["user_input"],
            "reference":           entry["reference"],
            "ground_truth_source": entry.get("ground_truth_source", ""),
            "expected_behavior":   entry.get("expected_behavior", ""),
            "notes":               entry.get("notes", ""),
            "agent_answer":        data["answer"],
            "agent_reasoning":     data["agent_reasoning"],
            "retrieved_contexts":  data["ragas_contexts"],
            "rule_ids_matched":    [
                r.get("rule_id") for r in data["rule_thresholds"] if r.get("rule_id")
            ],
            "nova_vs_phrma":       data["nova_vs_phrma"],
            "ragas_scores": {
                k: (round(float(v), 4) if _is_finite(v) else None)
                for k, v in scores.items()
            },
            "diagnostics": data["diagnostics"],
        }
        if data["error"]:
            record["error"] = data["error"]
        all_results.append(record)

    # ── Step 4: Summary + notable observations ────────────────────────────────

    # Load previous baseline for delta comparison (optional)
    prev_summary: dict | None = None
    if _PREV_BASELINE.exists():
        try:
            prev_summary = json.loads(_PREV_BASELINE.read_text())
        except Exception:
            pass

    summary  = build_summary(all_results)
    notable  = build_notable_observations(all_results)
    md_text  = build_summary_md(summary, notable, prev_summary=prev_summary)

    # ── Step 5: Write outputs ─────────────────────────────────────────────────

    ts      = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _RESULTS_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "results.json").write_text(
        json.dumps(all_results, indent=2, default=str)
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )
    (out_dir / "summary.md").write_text(md_text)

    print(f"\nResults written to {out_dir}/")
    print(f"  results.json  ({len(all_results)} entries)")
    print(f"  summary.json")
    print(f"  summary.md\n")

    # ── Step 6: Print summary ─────────────────────────────────────────────────

    print("=" * 72)
    print(md_text)
    print("=" * 72)

    # ── Step 7: CI gates (rule_backed only) ───────────────────────────────────

    failures = run_ci_gates(summary)
    if failures:
        print("\nCI GATE FAILURES (rule_backed):")
        for msg in failures:
            print(f"  {msg}")
        sys.exit(1)
    else:
        print("\nAll CI gates PASSED (rule_backed category).")


if __name__ == "__main__":
    main()
