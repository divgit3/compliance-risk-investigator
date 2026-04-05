# Phase 3 Implementation: AI Agents + FastAPI Backend

## Overview

Phase 3 adds an AI agent layer and REST API on top of the Phase 2
anomaly detection outputs. Three LangChain agents translate raw risk
scores, rule flags, and policy documents into structured, auditable
compliance reports. A FastAPI backend exposes all Phase 2 outputs and
agent endpoints to the Phase 4 Streamlit UI.

**Branch:** feature/phase3-ai-agents
**Stack additions:** langchain>=0.3.0, langchain-openai>=0.2.0, langchainhub,
                    fastapi>=0.111.0, uvicorn>=0.30.0, shap, httpx,
                    qdrant-client>=1.9.0, openai>=1.40.0

---

## Architecture

```
Phase 2 Outputs (parquet)          Qdrant (policy_docs)
│                                  │
▼                                  ▼
┌───────────────────────────────────────────────────────────┐
│                    LangChain Agents                        │
│  InvestigationAgent  MonitoringAgent  PolicyAgent          │
└───────────────────────────────────────────────────────────┘
                         │
                         ▼
              FastAPI Backend (api/)
                         │
                         ▼
            Phase 4 Streamlit UI (Task 4.x)
```

Data flow:
- Agents read Phase 2 parquets (read-only, no DB writes)
- Agents call Qdrant for policy grounding (policy_docs collection, 128 chunks)
- FastAPI serves parquet data + agent outputs as JSON
- MLflow logs every agent run (experiment per agent)

---

## Task 3.1: Investigation Agent

**File:** `agents/investigation_agent.py`
**Status:** ✅ Complete

### Purpose

Investigates a single HCP given their hcp_id. Orchestrates five tools via a
ReAct loop and returns a structured `InvestigationReport` with full policy
citations and audit trail.

### Agent Pattern

ReAct (Reason + Act) via LangChain `create_react_agent` + `AgentExecutor`.
`max_iterations=8`, `handle_parsing_errors=True`.
Model: `gpt-4o-mini` (configurable).
Prompt: LangChain Hub `hwchase17/react` with Nova Pharma system prefix prepended.

### Tools

| Tool | Source | Purpose |
|------|--------|---------|
| `get_hcp_risk_profile` | risk_scores.parquet + feature_store_raw.parquet | Risk score, tier, key metrics |
| `get_rule_flags` | rule_flags.parquet + compliance/rules.json | Fired flags with policy citations |
| `get_peer_benchmark` | hcp_spend_raw_dollars.parquet + feature_store_raw.parquet | Percentile rank vs specialty peers |
| `get_top_anomalous_features` | feature_importance.csv + feature_store_raw.parquet | Top 5 IF-driving features |
| `search_policy_docs` | Qdrant localhost:6333 — policy_docs collection | Policy grounding + chunk citations |

### Tool Implementation Details

**Data loading:** All tools use a module-level `_CACHE` dict. Parquets are
loaded once on first call and reused across all investigations in the same
process. `rules.json` is also cached.

**Flag → rule mapping:** `_FLAG_RULE_MAP` in `data_tools.py` maps each of the
23 `flag_*` columns to one or more `rule_id` values from `rules.json`.
Citations include the rule name, authority, and chunk_id.

**Peer benchmark fallback:** If fewer than 10 specialty peers exist (or
`specialty=None`), falls back to the full 97,011-HCP population.

**IF-excluded features:** `get_top_anomalous_features` excludes
`interaction_frequency_score`, `data_completeness_score`,
`has_speaker_events`, `has_cms_payments`, `has_interactions` — these were
excluded from Isolation Forest training to avoid circular signals and activity
proxy contamination.

### Output: InvestigationReport

```python
InvestigationReport(
    hcp_id,                     # str
    generated_at,               # datetime (UTC)
    risk_score,                 # float 0–100
    risk_tier,                  # str: critical | high | medium | low
    rule_score,                 # float 0–100
    if_score,                   # float 0–100 (anomaly_score)
    score_explanation,          # LLM narrative
    rule_flags,                 # List[RuleFlag] — fired flags only
    peer_benchmark,             # PeerBenchmark — percentile rank
    top_anomalous_features,     # List[AnomalousFeature] — top 5
    policy_citations,           # List[PolicyCitation] — from Qdrant
    recommended_action,         # str — deterministic from risk_tier
    action_rationale,           # LLM narrative
    agent_reasoning             # str — full ReAct chain for audit
)
```

### Recommended Action Logic (deterministic, not LLM)

| Risk Tier | Recommended Action |
|-----------|-------------------|
| critical  | investigate |
| high      | review |
| medium    | monitor |
| low       | continue |

### Risk Tier Thresholds (Phase 2 scorer.py)

| Tier | Score Range | HCPs | % |
|------|-------------|------|---|
| critical | ≥60.0 | 291 | 0.3% |
| high | ≥25.0 | 8,055 | 8.3% |
| medium | ≥10.0 | 21,103 | 21.8% |
| low | <10.0 | 67,562 | 69.6% |

Note: Critical-flag floor applies — HCPs with any critical-severity flag are
elevated to at least `high` tier regardless of score.

### Error Handling

- All tools return `{"error": "..."}` on any exception
- `AgentExecutor(handle_parsing_errors=True)` absorbs LLM formatting errors
- `investigate()` wraps the full agent call in try/except
- On exception: fallback report built from direct `risk_scores.parquet` read
  with `score_explanation = f"Agent error: {e}"`
- MLflow logging wrapped in try/except — logging failure never crashes investigation

### MLflow Tracking

Experiment: `investigation_agent`

Per-run logging:
- params: `hcp_id`, `model`, `risk_tier`
- metrics: `risk_score`, `num_flags`, `latency_ms`
- tags: `recommended_action`, `phase=3`

### Schemas (agents/schemas.py)

Shared Pydantic v2 models used across all three agents:

```python
PolicyCitation(chunk_id, source_doc, relevance_score, excerpt)
RuleFlag(flag_name, flag_value, threshold, policy_citation, severity)
PeerBenchmark(percentile_rank, peer_avg_total_spend, peer_max_total_spend,
              hcp_total_spend, specialty, state)
AnomalousFeature(feature_name, hcp_value, importance_score, pearson_r, direction)
InvestigationReport(...)   # full report — see above
MonitoringReport(...)      # Task 3.2 (stub)
PolicyAnswer(...)          # Task 3.3 (stub)
```

### Excluded Features (carry from Phase 2)

These features are excluded from `get_top_anomalous_features` because they
were excluded from Isolation Forest training:

| Feature | Reason excluded |
|---------|----------------|
| `interaction_frequency_score` | Circular — is itself a composite heuristic score |
| `data_completeness_score` | Activity proxy, not a spend/compliance signal |
| `has_speaker_events` | Binary activity flag — not anomalous in IF sense |
| `has_cms_payments` | Binary activity flag |
| `has_interactions` | Binary activity flag |

### How to Run (smoke test)

```bash
source venv/bin/activate
export OPENAI_API_KEY=sk-...

# Qdrant must be running: localhost:6333
# MLflow must be running: localhost:5001

python agents/test_investigation.py
```

The smoke test selects one critical-tier, one high-tier, and one low-tier HCP
from `risk_scores.parquet` and prints the full `InvestigationReport` JSON for
each, plus total elapsed time.

### Known Limitations

- SHAP not yet implemented — feature importance uses global Pearson proxy from
  `feature_importance.csv`; Task 3.7 will replace with per-HCP SHAP values
- Industry benchmarks incomplete — `engagement_priority_score` capped at 45pts
  until Task 3.5 loads `mart_competitor_payments` + `mart_population_payments`
- `peer_benchmark` uses specialty-only filter; no geographic sub-filter within
  specialty (falls back to full population if <10 specialty peers)
- Peer spend comparison uses total spend (2022+2023+2024 sum); does not
  separate by program year (OIG uses annual basis)
- LangChain 0.2.5 is installed in venv; `requirements.txt` now specifies
  `>=0.3.0` — upgrade required before production deployment

---

## Task 3.2: Monitoring Agent

**File:** `agents/monitoring_agent.py`
**Tools file:** `agents/tools/monitoring_tools.py`
**Status:** ✅ Complete

### Purpose

Population-level compliance monitoring across the 97,011 HCP universe.
Accepts optional specialty, state, and risk_tier filters. Returns a
structured `MonitoringReport` with risk distribution, top flags, high-risk
segments, systemic issues, and a compliance-officer-facing narrative.

### Agent Pattern

ReAct (Reason + Act) via LangChain `create_react_agent` + `AgentExecutor`.
`max_iterations=6`, `handle_parsing_errors=True`.
Model: `gpt-4o-mini` (configurable).

### Tools

| Tool | Source | Purpose |
|------|--------|---------|
| `get_risk_distribution` | risk_scores.parquet + feature_store_raw.parquet | Tier counts, percentages, avg/median risk score |
| `get_flag_patterns` | rule_flags.parquet + rules.json | Top flags by HCP count and rate |
| `get_high_risk_segments` | risk_scores + feature_store_raw + rule_flags | Specialty and state segments ranked by critical rate |
| `detect_systemic_issues` | risk_scores + feature_store_raw + rule_flags | Deterministic pattern detection (4 issue types) |

### Output: MonitoringReport (actual schema)

```python
MonitoringReport(
    generated_at,               # datetime (UTC)
    scope_description,          # human-readable filter summary
    specialty_filter,           # Optional[str]
    state_filter,               # Optional[str]
    risk_tier_filter,           # Optional[str]
    total_hcps_in_scope,        # int
    risk_distribution,          # RiskDistribution
    top_flags,                  # List[FlagTrend] — top 10 by rate
    high_risk_segments,         # List[SegmentRisk] — top 5 specialty + top 5 state
    systemic_issues,            # List[SystemicIssue]
    summary_narrative,          # LLM-generated for compliance officers
    data_limitations,           # List[str] — always populated
    agent_reasoning             # full ReAct chain for audit
)
```

Supporting schemas (in `agents/schemas.py`):

```python
RiskDistribution(critical_count, high_count, medium_count, low_count,
                 total_count, critical_pct, high_pct, medium_pct, low_pct,
                 avg_risk_score, median_risk_score)

FlagTrend(flag_name, count, rate, policy_citation, severity)

SegmentRisk(segment_type, segment_value, hcp_count, critical_count,
            high_count, critical_rate, high_rate, avg_risk_score, top_flag)

SystemicIssue(issue_type, description, affected_hcp_count, severity,
              top_flags, recommendation)
```

### Systemic Issue Detection (deterministic)

Four issue types checked — all thresholds and recommendations are templated
strings, never LLM-generated:

| Issue Type | Condition | Severity | Threshold |
|------------|-----------|----------|-----------|
| `critical_cluster` | specialty or state with >2% critical rate AND ≥10 HCPs | critical | 2% |
| `high_flag_rate_specialty` | specialty where >30% HCPs have ≥2 flags AND ≥10 HCPs | high | 30% |
| `high_flag_rate_state` | state where >25% HCPs have ≥2 flags AND ≥10 HCPs | high | 25% |
| `dominant_flag_pattern` | single flag accounts for >40% of all fired flag instances | medium | 40% |

With current synthetic data: only `dominant_flag_pattern` fires
(`flag_fmv_non_compliance` at 45% of all fired flag instances).

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Segment minimum 5 HCPs | Avoid alarming on single-HCP specialties/states |
| Systemic thresholds deterministic | Audit-traceable; compliance officers need predictable rules |
| No temporal filtering | Synthetic data has no real dates — 2024 snapshot only |
| `data_limitations` always populated | Compliance officers must see known data gaps |
| `_FLAG_RULE_MAP` pattern reused | Consistent citation logic across all agents |
| Specialty filter graceful fallback | specialty=None for all HCPs in DuckDB dev; tool returns descriptive error |

### Data Limitations (always included in every MonitoringReport)

1. No temporal data — report reflects 2024 snapshot only; trend analysis not possible
2. `np_escalating_rank` 0-filled (Athena/DuckDB split) — excluded from analysis
3. `engagement_priority_score` capped at 45/100 — incomplete until Task 3.5
4. Peer benchmarks: specialty-only filter, no geographic sub-filter
5. `specialty` field is NULL for all HCPs in DuckDB dev — specialty segments unavailable

### MLflow Tracking

Experiment: `monitoring_agent`

Per-run logging:
- params: `specialty_filter`, `state_filter`, `risk_tier_filter`, `model`
- metrics: `total_hcps_in_scope`, `critical_count`, `high_count`,
           `num_systemic_issues`, `latency_ms`
- tags: `phase=3`, `task=3.2`

### How to Run (smoke test)

```bash
source venv/bin/activate
export OPENAI_API_KEY=sk-...

python agents/test_monitoring.py
```

Runs three scenarios: full population, top state by HCP count, state with
most critical-tier HCPs (all derived from parquets at runtime).

---

## Task 3.3: Policy Agent

**File:** `agents/policy_agent.py`
**Tools file:** `agents/tools/policy_tools.py` (added `lookup_rule` in Task 3.3)
**Status:** ✅ Complete

### Purpose

RAG agent over the Qdrant `policy_docs` collection (128 chunks, 5 documents).
Answers natural language compliance questions with precise citations, exact rule
thresholds from `rules.json`, and Nova Pharma vs PhRMA comparisons.

### Agent Pattern

ReAct (Reason + Act) via LangChain `create_react_agent` + `AgentExecutor`.
`max_iterations=6`, `handle_parsing_errors=True`.
Model: `gpt-4o-mini` (configurable).
2 tools only — no HCP data tools (policy questions need citations, not records).

### Tools

| Tool | File | Purpose |
|------|------|---------|
| `search_policy_docs` | agents/tools/policy_tools.py | Qdrant semantic search over 128 policy chunks |
| `lookup_rule` | agents/tools/policy_tools.py | Exact threshold lookup from compliance/rules.json |

**`lookup_rule` implementation:** Keyword token-overlap scoring against `rule_id`,
`rule_name`, `category`, `violation_type`, and `applies_to` fields. Returns top 5
matches with exact thresholds, authority, `chunk_id` for audit, and `nova_override`
flag. `nova_override=True` when Nova's effective threshold is lower (stricter) than
the `fallback_rules` entry (proxy for PhRMA industry standard).

### Output: PolicyAnswer (actual schema)

```python
PolicyAnswer(
    question,                   # str
    generated_at,               # datetime (UTC)
    answer,                     # LLM narrative grounded in citations
    relevant_chunks,            # List[PolicyCitation] from Qdrant
    rule_thresholds,            # List[dict]: rule_id, rule_name, threshold, authority, chunk_id
    nova_vs_phrma,              # List[NovaVsPhRMA] — only when nova_override=True
    chunk_ids_for_audit,        # List[str] deduplicated across all tool calls
    confidence,                 # str: "high" | "medium" | "low" (deterministic)
    data_limitations,           # List[str] keyed to question content
    agent_reasoning             # str: full ReAct chain for audit
)
```

Supporting schema:

```python
NovaVsPhRMA(
    rule_name,          # str
    phrma_threshold,    # Optional[str] — fallback_rules value as PhRMA proxy
    nova_threshold,     # str — effective Nova Pharma threshold
    nova_is_stricter,   # bool — always True when this object is created
    source_rule_id      # str — rules.json rule_id
)
```

### Confidence Assignment (deterministic)

| Condition | Confidence |
|-----------|-----------|
| ≥1 Qdrant chunk AND ≥1 rule matched | high |
| Either Qdrant OR rules, but not both | medium |
| Neither tool returned results | low |

### Nova Pharma Overrides (from `lookup_rule` data)

Detected by comparing `effective_threshold` against `fallback_rules` (PhRMA proxy):

| Rule | Nova Threshold | PhRMA Equivalent | Nova Override |
|------|---------------|-----------------|---------------|
| MEAL_001 Meal — Breakfast | $25 | $30 | ✅ |
| MEAL_002 Meal — Lunch | $50 | $75 | ✅ |
| MEAL_003 Meal — Dinner | $100 | $125 | ✅ |
| MEAL_004 Meal — General | $75 | $75 | — |
| SPEAKER_001 Speaker FMV Cap | $3,500 | $4,000 | ✅ |
| VENUE_003 Meal Cost/Attendee | $100 | $125 | ✅ |
| COMP_001 Annual Speaker Cap | $75,000 | $75,000 | — |

### Policy Knowledge Base

| Document | Authority | Chunks |
|----------|-----------|--------|
| PhRMA Code on Interactions with HCPs (2022) | PhRMA | ~30 |
| OIG Compliance Program Guidance | OIG | ~35 |
| OIG Speaker Program Special Fraud Alert | OIG | ~20 |
| CMS Open Payments Data Dictionary | CMS | ~15 |
| Nova Pharma Internal Compliance Policy | Nova Pharma | ~28 |
| **Total** | | **128** |

### Data Limitations (keyed to question content)

Always included:
- "Policy knowledge base reflects documents as of 2022-2024 snapshot"

Triggered by keyword matching:
- Question mentions "benchmark/industry/competitor" → engagement_priority_score caveat
- Question mentions "trend/over time/year" → no temporal data caveat
- Question mentions "SHAP/feature importance/why flagged" → Pearson proxy caveat

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| 2 tools only (no data tools) | Policy questions need citations, not HCP records |
| `confidence` is deterministic | Audit-traceable — not subject to LLM optimism |
| `lookup_rule` before `search_policy_docs` (in system prompt) | Exact thresholds anchor the narrative; reduces hallucination |
| `nova_vs_phrma` only when `nova_override=True` | Avoids false equivalences for rules where PhRMA has no threshold |
| `chunk_ids_for_audit` deduplicated across all tool calls | Audit trail must not double-count sources |
| `data_limitations` keyed to question content | Relevant caveats only, not generic boilerplate |
| `fallback_rules` as PhRMA proxy | `rules.json` industry_value is sparse; fallback_rules represent pre-override defaults |

### MLflow Tracking

Experiment: `policy_agent`

Per-run logging:
- params: `question` (first 100 chars), `model`
- metrics: `num_chunks_retrieved`, `num_rules_matched`, `num_nova_overrides`, `latency_ms`
- tags: `confidence`, `phase=3`, `task=3.3`

### How to Run (smoke test)

```bash
source venv/bin/activate
export OPENAI_API_KEY=sk-...
# Qdrant must be running: localhost:6333

python agents/test_policy.py
```

Runs 6 questions: meal limits, FMV cap comparison, vague rationale, risk scoring,
OIG speaker fraud, and an edge-case question potentially outside the knowledge base.

---

## Task 3.4: FastAPI Backend

**File:** `api/main.py` + `api/routers/`
**Status:** 🔲 Planned

### Endpoints

| Method | Path | Handler | Source |
|--------|------|---------|--------|
| GET | `/health` | health check | system |
| GET | `/hcps` | list HCPs with risk scores + filters | risk_scores.parquet |
| GET | `/hcps/{hcp_id}` | full HCP risk profile | risk_scores + feature_store_raw |
| GET | `/hcps/{hcp_id}/investigate` | InvestigationAgent | Task 3.1 |
| GET | `/hcps/{hcp_id}/flags` | rule flags + citations | rule_flags + rules.json |
| GET | `/events` | speaker events with risk | event_feature_matrix.parquet |
| GET | `/monitoring` | MonitoringAgent report | Task 3.2 |
| POST | `/policy/query` | PolicyAgent RAG | Task 3.3 |
| GET | `/benchmarks/{hcp_id}` | peer benchmark | Task 3.5 |

### Design Decisions

- Parquet files loaded once at startup into module-level DataFrames
  (FastAPI `lifespan` context manager)
- Agent endpoints are `async` — `InvestigationAgent.investigate()` awaited
- `/hcps` supports query params: `specialty`, `state`, `risk_tier`, `limit`, `offset`
- All responses are Pydantic models from `agents/schemas.py`
- CORS enabled for `localhost:8501` (Streamlit)

---

## Task 3.5: Industry Benchmarks

**Status:** 🔲 Planned

### Problem

`engagement_priority_score` is capped at 45/100 because:
- `mart_competitor_payments` (Athena) not yet loaded at Python layer
- `mart_population_payments` (Athena) not yet loaded at Python layer
- SOW (share of wallet) and industry ratios are 0-filled

### Plan

1. Load `mart_competitor_payments` from Athena via boto3/awswrangler
2. Load `mart_population_payments` from Athena
3. Compute per-HCP SOW: `nova_spend / (nova_spend + competitor_spend)`
4. Compute industry ratios vs population baseline
5. Update `engagement_priority_score` to full 100pts
6. Wire into `/benchmarks/{hcp_id}` FastAPI endpoint
7. Re-run `features/feature_store.py` with updated benchmarks

---

## Task 3.6: Model Comparison (Optional)

**Status:** 🔲 Planned

Compare Isolation Forest vs LOF vs OCSVM on 2024 holdout:
- Split `feature_store.parquet` by year (2024 = holdout)
- Train IF, LOF, OCSVM on 2022–2023
- Evaluate all on 2024 vs `ground_truth_labels.parquet`
- Log all experiments to MLflow: experiment `model_comparison`
- Select best model by `recall_high_critical`
- Update `scorer.py` to use best model

---

## Task 3.7: SHAP Explanations

**Status:** 🔲 Planned

Replace Pearson proxy in `feature_importance.csv` with true SHAP values:
- Add `shap.TreeExplainer` to `models/isolation_forest.py`
- Compute per-HCP SHAP values (97,011 × 99 features)
- Save `shap_values.parquet` to `models/outputs/`
- Update `get_top_anomalous_features` tool to use SHAP per-HCP values
  instead of global `feature_importance.csv`

Impact: Investigation Agent will show HCP-specific feature drivers instead of
global importance rankings, significantly improving report quality.

---

## Task 3.8: API Tests

**File:** `tests/test_api.py`
**Status:** 🔲 Planned

pytest suite using `httpx.AsyncClient` against FastAPI test client:
- `test_health_check`
- `test_list_hcps_default`
- `test_list_hcps_filter_by_tier`
- `test_get_hcp_profile_valid`
- `test_get_hcp_profile_not_found`
- `test_get_hcp_flags`
- `test_get_events`
- `test_policy_query`
- `test_benchmarks`
- `test_investigate_critical_hcp` (spot-check report structure)

---

## Task 3.9: Docker

**Status:** 🔲 Planned

`docker/Dockerfile`: FastAPI + uvicorn, `python:3.12-slim`

`docker/docker-compose.yml` services:
- `api`: FastAPI on port 8000
- `mlflow`: MLflow on port 5001 with sqlite backend
- `qdrant`: Qdrant on port 6333 with local volume

Volumes: mount `features/outputs/` and `models/outputs/` into api container.

---

## Task 3.10: Commit + README

**Status:** 🔲 Planned

- Update root `README.md` with Phase 3 architecture diagram
- Document `OPENAI_API_KEY` setup in `.env.example`
- Document how to run: `uvicorn api.main:app --reload`
- Merge `feature/phase3-ai-agents` → `main`
- Tag: `v3.0.0`

---

## Phase 3 Metrics Targets

| Metric | Target |
|--------|--------|
| Investigation report latency | < 15s (gpt-4o-mini) |
| Policy query latency | < 5s |
| API p95 latency (/hcps list) | < 200ms |
| API test coverage | ≥ 80% of endpoints |
| MLflow runs logged | 100% of agent invocations |
| engagement_priority_score | 100pts after Task 3.5 |

---

## Running Phase 3

```bash
# Prerequisites
export OPENAI_API_KEY=sk-...
# Qdrant must be running: localhost:6333
# MLflow must be running: localhost:5001

# Install new dependencies
pip install -r requirements.txt

# Smoke test Investigation Agent (Task 3.1)
python agents/test_investigation.py

# Start FastAPI (Task 3.4)
uvicorn api.main:app --reload --port 8000

# Run API tests (Task 3.8)
pytest tests/test_api.py -v

# Docker (Task 3.9)
docker-compose up --build
```

---

## File Structure

```
compliance-risk-investigator/
├── agents/
│   ├── __init__.py
│   ├── schemas.py                  # Pydantic models (all agents) ✅
│   ├── investigation_agent.py      # Task 3.1 ✅
│   ├── monitoring_agent.py         # Task 3.2 ✅
│   ├── policy_agent.py             # Task 3.3 ✅
│   ├── test_investigation.py       # Task 3.1 smoke test ✅
│   ├── test_monitoring.py          # Task 3.2 smoke test ✅
│   └── test_policy.py              # Task 3.3 smoke test ✅
│   └── tools/
│       ├── __init__.py
│       ├── data_tools.py           # Task 3.1 ✅
│       ├── monitoring_tools.py     # Task 3.2 ✅
│       └── policy_tools.py         # Task 3.1 ✅
├── api/
│   ├── __init__.py
│   ├── main.py                     # Task 3.4 🔲
│   ├── routers/
│   │   ├── hcps.py
│   │   ├── events.py
│   │   ├── monitoring.py
│   │   ├── policy.py
│   │   └── benchmarks.py
│   └── dependencies.py
├── tests/
│   ├── test_anomaly_models.py      # Phase 2 ✅ 50/50 passing
│   └── test_api.py                 # Task 3.8 🔲
├── docker/
│   ├── Dockerfile                  # Task 3.9 🔲
│   └── docker-compose.yml
└── docs/
    └── implementation/
        ├── phase1_implementation.md
        ├── phase2_implementation.md
        └── phase3_implementation.md ✅
```
