"""
tests/trustworthy_ai/test_latency.py — Attribute #7: Latency benchmark

Measures wall-clock response times for all three agents across a pool of
representative queries. Part of the Trustworthy AI evaluation framework
documented in LOCAL_SETUP_NOTES.md.

Usage:
    # Against local API (default)
    python -m tests.trustworthy_ai.test_latency

    # Against Dockerised API from inside the same network
    API_BASE_URL=http://api:8000 python -m tests.trustworthy_ai.test_latency

    # Customise run
    python -m tests.trustworthy_ai.test_latency --n 5 --agents policy_agent monitoring_agent
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# ── Paths ──────────────────────────────────────────────────────────────────────

_HERE        = Path(__file__).resolve().parent
_QUERIES_FILE = _HERE / "queries" / "latency_queries.json"

# ── Constants ──────────────────────────────────────────────────────────────────

_ALL_AGENTS  = ["policy_agent", "monitoring_agent", "investigation_agent"]
_REQUEST_TIMEOUT = 60.0  # seconds per request


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _call_policy(client: httpx.Client, base_url: str, question: str) -> None:
    """POST /policy/query"""
    client.post(
        f"{base_url}/policy/query",
        json={"question": question},
        timeout=_REQUEST_TIMEOUT,
    ).raise_for_status()


def _call_monitoring(client: httpx.Client, base_url: str, params: dict) -> None:
    """GET /monitoring with optional query params"""
    client.get(
        f"{base_url}/monitoring",
        params={k: v for k, v in params.items() if v is not None},
        timeout=_REQUEST_TIMEOUT,
    ).raise_for_status()


def _call_investigation(client: httpx.Client, base_url: str, hcp_id: str) -> None:
    """GET /hcps/{hcp_id}/investigate"""
    client.get(
        f"{base_url}/hcps/{hcp_id}/investigate",
        timeout=_REQUEST_TIMEOUT,
    ).raise_for_status()


# ── Core measurement ───────────────────────────────────────────────────────────

def _measure(
    client: httpx.Client,
    base_url: str,
    agent: str,
    query: dict,
    n: int,
) -> tuple[list[Optional[float]], list[str]]:
    """
    Run a single query N times. Returns (latencies_ms, errors).
    latencies_ms[i] is None if request i failed.
    """
    latencies: list[Optional[float]] = []
    errors: list[str] = []

    for _ in range(n):
        t0 = time.perf_counter()
        try:
            if agent == "policy_agent":
                _call_policy(client, base_url, query["question"])
            elif agent == "monitoring_agent":
                _call_monitoring(client, base_url, query.get("params", {}))
            elif agent == "investigation_agent":
                _call_investigation(client, base_url, query["hcp_id"])
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
        except Exception as exc:
            latencies.append(None)
            errors.append(str(exc))

    return latencies, errors


# ── Statistics ─────────────────────────────────────────────────────────────────

def _compute_stats(latencies: list[Optional[float]]) -> dict:
    """Compute summary statistics from a list of latencies (None = failed)."""
    good = [x for x in latencies if x is not None]
    n_total   = len(latencies)
    n_success = len(good)
    n_fail    = n_total - n_success

    if not good:
        return {
            "n_total":   n_total,
            "n_success": 0,
            "n_fail":    n_fail,
            "mean_ms":   None,
            "median_ms": None,
            "p95_ms":    None,
            "p99_ms":    None,
            "std_ms":    None,
        }

    sorted_ms = sorted(good)

    def _percentile(data: list[float], pct: float) -> float:
        k = (len(data) - 1) * pct / 100
        lo, hi = int(k), min(int(k) + 1, len(data) - 1)
        return data[lo] + (data[hi] - data[lo]) * (k - lo)

    return {
        "n_total":   n_total,
        "n_success": n_success,
        "n_fail":    n_fail,
        "mean_ms":   round(statistics.mean(good), 1),
        "median_ms": round(statistics.median(good), 1),
        "p95_ms":    round(_percentile(sorted_ms, 95), 1),
        "p99_ms":    round(_percentile(sorted_ms, 99), 1),
        "std_ms":    round(statistics.stdev(good), 1) if len(good) > 1 else 0.0,
    }


# ── Report formatting ──────────────────────────────────────────────────────────

def _print_summary(per_query: dict, per_agent: dict) -> None:
    """Print a human-readable table to stdout."""
    col_w = [24, 16, 10, 10, 12]
    header = (
        f"{'Agent':<{col_w[0]}} {'Query':<{col_w[1]}} "
        f"{'p95 (ms)':>{col_w[2]}} {'mean (ms)':>{col_w[3]}} "
        f"{'success':>{col_w[4]}}"
    )
    sep = "-" * sum(col_w + [4])  # account for spaces

    print()
    print("=" * len(sep))
    print("  Latency Benchmark — Attribute #7 (Trustworthy AI)")
    print("=" * len(sep))
    print(header)
    print(sep)

    for agent, queries in per_query.items():
        for qid, result in queries.items():
            s = result["stats"]
            p95  = f"{s['p95_ms']:.0f}" if s["p95_ms"] is not None else "—"
            mean = f"{s['mean_ms']:.0f}" if s["mean_ms"] is not None else "—"
            succ = f"{s['n_success']}/{s['n_total']}"
            print(
                f"{agent:<{col_w[0]}} {qid:<{col_w[1]}} "
                f"{p95:>{col_w[2]}} {mean:>{col_w[3]}} {succ:>{col_w[4]}}"
            )
        # Agent aggregate row
        agg = per_agent[agent]
        p95  = f"{agg['p95_ms']:.0f}"  if agg["p95_ms"]  is not None else "—"
        mean = f"{agg['mean_ms']:.0f}" if agg["mean_ms"] is not None else "—"
        succ = f"{agg['n_success']}/{agg['n_total']}"
        print(
            f"{'  [' + agent + ' total]':<{col_w[0]}} {'':<{col_w[1]}} "
            f"{p95:>{col_w[2]}} {mean:>{col_w[3]}} {succ:>{col_w[4]}}"
        )
        print(sep)

    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def run(n: int, agents: list[str], base_url: str, output_dir: Path) -> dict:
    run_ts   = datetime.now(timezone.utc)
    ts_str   = run_ts.strftime("%Y%m%dT%H%M%SZ")
    wall_t0  = time.perf_counter()

    queries_raw: dict = json.loads(_QUERIES_FILE.read_text())

    per_query: dict  = {}
    per_agent: dict  = {}
    all_latencies: list[Optional[float]] = []

    with httpx.Client() as client:
        for agent in agents:
            if agent not in queries_raw:
                print(f"[WARN] No queries defined for '{agent}' — skipping", file=sys.stderr)
                continue

            per_query[agent] = {}
            agent_latencies: list[Optional[float]] = []

            for query in queries_raw[agent]:
                qid = query["id"]
                print(f"  {agent} / {qid} ({n} runs)...", end=" ", flush=True)

                latencies_ms, errors = _measure(client, base_url, agent, query, n)

                if errors:
                    print(f"[{len(errors)} error(s): {errors[0][:80]}]", end=" ")

                stats = _compute_stats(latencies_ms)
                per_query[agent][qid] = {
                    "raw_latencies_ms": [round(x, 1) if x is not None else None for x in latencies_ms],
                    "stats": stats,
                    "errors": errors,
                }
                agent_latencies.extend(latencies_ms)

                p95_str = f"{stats['p95_ms']:.0f} ms" if stats["p95_ms"] is not None else "all failed"
                print(f"p95={p95_str}")

            per_agent[agent] = _compute_stats(agent_latencies)
            all_latencies.extend(agent_latencies)

    total_elapsed_s = time.perf_counter() - wall_t0
    overall_summary = _compute_stats(all_latencies)

    report = {
        "metadata": {
            "run_timestamp":    run_ts.isoformat(),
            "api_base_url":     base_url,
            "n_per_query":      n,
            "agents_tested":    agents,
            "total_elapsed_s":  round(total_elapsed_s, 1),
        },
        "per_query_results":  per_query,
        "per_agent_summary":  per_agent,
        "overall_summary":    overall_summary,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"latency_{ts_str}.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Report saved: {report_path}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Latency benchmark — Trustworthy AI Attribute #7"
    )
    parser.add_argument(
        "--n",
        type=int,
        default=10,
        metavar="N",
        help="Number of requests per query (default: 10)",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        default=_ALL_AGENTS,
        choices=_ALL_AGENTS,
        metavar="AGENT",
        help=f"Agents to benchmark (default: all). Choices: {_ALL_AGENTS}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_HERE / "reports",
        metavar="DIR",
        help="Directory for JSON report output (default: tests/trustworthy_ai/reports)",
    )
    args = parser.parse_args()

    base_url = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")

    print(f"Latency benchmark starting")
    print(f"  API:    {base_url}")
    print(f"  N:      {args.n} requests per query")
    print(f"  Agents: {', '.join(args.agents)}")
    print()

    report = run(
        n=args.n,
        agents=args.agents,
        base_url=base_url,
        output_dir=args.output_dir,
    )

    _print_summary(report["per_query_results"], report["per_agent_summary"])
    sys.exit(0)


if __name__ == "__main__":
    main()
