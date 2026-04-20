# Trustworthy AI Evaluation — Attribute #7: Latency

Measures wall-clock response latency for the three agents in the Compliance Risk
Investigator, as part of the 11-attribute Trustworthy AI framework documented in
`LOCAL_SETUP_NOTES.md`.

## What it measures

For each agent and each query in `queries/latency_queries.json`, the script sends
N HTTP requests (default 10) and records wall-clock response time per request.
It computes: mean, median, p95, p99, std, n_success/n_total.

Results are aggregated per-query, per-agent, and overall.

## How to run

**Against local API (default `http://localhost:8000`):**
```bash
python -m tests.trustworthy_ai.test_latency
```

**Against Dockerised API** (run from inside a container on the same Docker network):
```bash
API_BASE_URL=http://api:8000 python -m tests.trustworthy_ai.test_latency
```

**Options:**
```bash
python -m tests.trustworthy_ai.test_latency --n 5                          # 5 runs per query
python -m tests.trustworthy_ai.test_latency --agents policy_agent          # single agent
python -m tests.trustworthy_ai.test_latency --output-dir /tmp/lat_reports  # custom output dir
```

## What the report JSON contains

Each run writes `reports/latency_<ISO timestamp>.json` with:

- **`metadata`** — run timestamp, API base URL, N, agents tested, total elapsed time
- **`per_query_results`** — nested `{agent: {query_id: {raw_latencies_ms, stats, errors}}}`
  where `stats` includes: `n_total`, `n_success`, `n_fail`, `mean_ms`, `median_ms`, `p95_ms`, `p99_ms`, `std_ms`
- **`per_agent_summary`** — same stats aggregated across all queries for each agent
- **`overall_summary`** — stats across all agents and queries combined

Failed or timed-out requests are recorded as `null` in `raw_latencies_ms` and counted
in `n_fail`; they are excluded from the computed statistics without stopping the run.

## Reports directory

`reports/` is tracked via `.gitkeep` but actual `*.json` report files are git-ignored.
Commit a reference report manually when you have a stable baseline worth preserving.
