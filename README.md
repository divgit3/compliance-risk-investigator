![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Phase 1](https://img.shields.io/badge/Phase%201-Complete-brightgreen)
![Phase 2](https://img.shields.io/badge/Phase%202-Complete-brightgreen)
![Phase 3](https://img.shields.io/badge/Phase%203-Complete-brightgreen)
![Phase 4](https://img.shields.io/badge/Phase%204-Complete-brightgreen)

# Compliance Risk Investigator AI

> Pharma HCP compliance analytics platform built for Nova Pharma Inc. (Takeda pseudonym)
> Anomaly detection · AI agents · Policy grounding · FastAPI backend

A production-style compliance analytics platform that detects anomalies in
pharmaceutical HCP interactions using real CMS Open Payments data and synthetic
internal compliance records. Ingests 18GB+ of public data, transforms it via dbt,
scores 97K HCPs with a rule-based + Isolation Forest dual detection engine, grounds
all flags in policy documents via RAG (LangChain + OpenAI + Qdrant), and serves
structured compliance reports through a FastAPI backend backed by three AI agents.

> **Data Notice:** All HCP identities are pseudonymized. Nova Pharma Inc is a
> fictional company (based on Takeda publicly reported data). No real proprietary
> data is used. CMS Open Payments data is public, sourced from
> [cms.gov](https://www.cms.gov/priorities/innovation/data-and-reports/2023/openpayments-data).

---

## Architecture

```
Phase 1 — Data ingestion
  CMS Open Payments CSVs (18GB) ──► S3 ──► Glue ──► Athena
  Synthetic HCP data (1.1M rows) ──► S3 (Parquet)
  Policy PDFs (5 docs) ──► S3 ──► Qdrant (128 chunks, 1536-dim)
           │
           ▼
Phase 2 — Anomaly detection
  dbt transforms ──► feature store (104 features, 97K HCPs)
  rule_based_flags.py (23 flags) + isolation_forest.py (200 trees)
  scorer.py ──► risk_scores.parquet (0–100 score, 4 tiers)
           │
           ▼
Phase 3 — AI agents + API                    Qdrant (policy_docs)
  InvestigationAgent ◄────────────────────────────┤
  MonitoringAgent    ◄────────────────────────────┤
  PolicyAgent        ◄────────────────────────────┘
           │
  FastAPI (9 endpoints) ──► MLflow audit trail
           │
           ▼
Phase 4 — Streamlit Dashboard
  5-page compliance UI · Network graph · HCP drill-down · Policy Q&A
```

**Stack:** Python 3.12 · dbt 1.8.3 · DuckDB · AWS S3/Glue/Athena · Qdrant
· MLflow 3.10.1 · scikit-learn · SHAP · OpenAI GPT-4o-mini · LangChain
· FastAPI · Streamlit · Docker

---

## Project Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Data pipeline + synthetic data + policy RAG | ✅ Complete |
| 2 | Anomaly detection (rule-based + IF + SHAP) | ✅ Complete |
| 3 | AI agents + FastAPI backend + Docker | ✅ Complete |
| 4 | Streamlit dashboard | ✅ Complete |

---

## Quick Start

**Prerequisites:** Python 3.12, Docker Desktop, AWS credentials, OpenAI API key

```bash
git clone <repo-url>
cd compliance-risk-investigator
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp docker/.env.example docker/.env   # add OPENAI_API_KEY + AWS credentials
```

**Generate data (run once):**

```bash
python features/feature_store.py          # 104-feature parquet (97K HCPs)
python models/isolation_forest.py         # risk_scores + rule_flags parquets
python features/industry_benchmarks.py    # competitor benchmark parquets
python pipelines/embed_policy_docs.py     # populate Qdrant policy_docs collection
```

**Start infrastructure:**

```bash
cd docker && docker-compose up -d         # Qdrant :6333 · MLflow :5002
```

**Start API:**

```bash
export OPENAI_API_KEY=sk-...
uvicorn api.main:app --reload --port 8000
# API docs: http://localhost:8000/docs
```

**Run tests:**

```bash
pytest tests/ -m "not agent" -v           # 80 tests, ~0.90s
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/hcps` | List HCPs sorted by risk score descending |
| GET | `/hcps/{hcp_id}` | Risk profile for a single HCP |
| GET | `/hcps/{hcp_id}/investigate` | Run InvestigationAgent — full compliance report |
| GET | `/hcps/{hcp_id}/flags` | Rule flags that fired for a single HCP |
| GET | `/events` | List event feature aggregates per HCP |
| GET | `/events/{hcp_id}` | Event feature aggregates for a single HCP |
| GET | `/monitoring` | Run MonitoringAgent — population-level risk analysis |
| GET | `/benchmarks/{hcp_id}` | Industry benchmark comparison for a single HCP |
| POST | `/policy/query` | PolicyAgent RAG — answer a natural language compliance question |

All agent endpoints (`/investigate`, `/monitoring`, `/policy/query`) are async and
call OpenAI — expect 5–30s latency. Non-agent endpoints return in <100ms.

---

## Phase 1 — Data Foundation ✅

| Component | Status | Details |
|-----------|--------|---------|
| CMS data pipeline | ✅ Complete | 18.4GB in S3, Glue cataloged, Athena queryable |
| Synthetic data | ✅ Complete | 1.1M+ records, 97K HCPs, 14 violation types |
| dbt models | ✅ Complete | 11 models, 51 tests passing (Athena + DuckDB) |
| Policy docs | ✅ Complete | 5 docs, 128 chunks in Qdrant |
| Qdrant | ✅ Running | localhost:6333, `policy_docs` collection ready |
| MLflow | ✅ Running | localhost:5001, tracking server ready |

**CMS Open Payments (2022–2024):**
- 18.4GB raw CSVs in S3 · ~18M physician payment records across 3 years
- Anchor company: Takeda (pseudonymized as Nova Pharma Inc)
- 4 competitors: Janssen, Merck, Amgen, BMS (pseudonymized)
- 473K Nova Pharma payments · 4.3M competitor · 13.2M population

**Synthetic Data:**
- 97K fictional HCPs · 1M+ interaction records · 14 violation types labeled
- Speaker program events + attendee records stored in S3 as Parquet

**Policy Documents (5 docs, 128 chunks):**

| Document | Source | Chunks |
|----------|--------|--------|
| PhRMA Code on Interactions with HCPs (2022) | Public | ~45 |
| OIG CPG: Pharmaceutical Manufacturers (2003) | Public | ~68 |
| OIG Special Fraud Alert: Speaker Programs (2020) | Public | ~12 |
| CMS Open Payments Data Dictionary | Public | ~44 |
| Nova Pharma Internal Compliance Policy | Synthetic | ~2 |

1536-dim vectors · Cosine similarity · stored in Qdrant `policy_docs` collection

---

## Phase 2 — Anomaly Detection Engine ✅

### Policy Grounding

Business rules extracted from policy documents via RAG — not hardcoded — ensuring
every flag is traceable to a specific policy chunk.

- **24 rules** extracted from 5 policy documents via OpenAI + Qdrant RAG
- **8 Nova Pharma overrides** (stricter thresholds than PhRMA industry code)
- Key thresholds: meal limit $25 (standard) / $50 (occasional) / $100 (severe),
  speaker FMV $3,500, annual cap $75,000
- Rules stored in `compliance/rules.json` with policy citations

### dbt Feature Marts (4 new models)

| Mart | Rows | Purpose |
|------|------|---------|
| mart_hcp_spend_features | 97,011 | CMS spend signals per HCP per year |
| mart_event_features | 5,241 | Speaker program event-level signals |
| mart_hcp_risk_profile | 97,011 | Master risk spine joining all features |
| mart_benchmark | 97,011 | Specialty peer benchmarking |

### Python Feature Engineering

- **hcp_spend_features.py** — Athena → 97,011-row scaled feature matrix
- **event_features.py** — 5,241 events → 1,354 HCP-level aggregated rows
- **feature_store.py** — 104 features · Athena/DuckDB split resolved at Python
  layer · ground truth kept strictly separate from feature matrix

### Anomaly Detection Models

| Script | Output | Description |
|--------|--------|-------------|
| rule_based_flags.py | rule_flags.parquet | 23 policy-traceable boolean flags per HCP |
| isolation_forest.py | if_scores.parquet | 200 trees · anomaly score 0–100 · 10% outlier rate |
| scorer.py | risk_scores.parquet | Unified 0–100 score: 60% rule + 40% IF |
| mlflow_tracking.py | mlflow_fallback_metrics.json | Params, metrics, artifacts, GT recall |

**Scoring formula:** `risk_score = 0.60 × rule_score + 0.40 × anomaly_score`

Severity weights: critical +40 · high +20 · medium +10 (rule_score capped at 100)

Critical-flag floor: HCPs with any critical flag assigned ≥ high tier regardless of score.

### Key Results

| Metric | Value |
|--------|-------|
| HCPs scored | 97,011 |
| Critical tier (≥75) | 479 (0.5%) |
| High tier (50–75) | 35,800 (36.9%) |
| Medium tier (25–50) | 4,977 (5.1%) |
| Low tier (<25) | 55,755 (57.5%) |
| IF outlier rate | 10.0% (9,701 HCPs) |
| GT recall — any flag | 92.6% |
| GT recall — high+critical tier | 41.0% |
| Top risk feature | combined_raw_risk_score (\|r\|=0.695) |
| Tests passing | 50/50 |

### Testing

- **50 pytest integration tests** across 7 test classes
- All passing in 0.67s on actual pipeline parquet outputs — no mocks
- Key assertions: violation count (23,727), outlier rate (10.0%), tier distribution,
  GT recall thresholds, feature importance correlation

---

## Phase 3 — AI Agents + FastAPI Backend ✅

| Task | Component | Status |
|------|-----------|--------|
| 3.1 | InvestigationAgent — per-HCP compliance report (5 tools) | ✅ Complete |
| 3.2 | MonitoringAgent — population risk analysis (4 tools) | ✅ Complete |
| 3.3 | PolicyAgent — Qdrant RAG compliance Q&A (2 tools) | ✅ Complete |
| 3.4 | FastAPI backend — 9 endpoints, lifespan loader | ✅ Complete |
| 3.5 | Industry benchmarks — Athena + EPS fallback (100 pts) | ✅ Complete |
| 3.7 | SHAP explanations — per-HCP feature contributions | ✅ Complete |
| 3.8 | API test suite — 80 tests, 0.90s CI-safe | ✅ Complete |
| 3.9 | Docker — Dockerfile + docker-compose, 11/11 smoke tests | ✅ Complete |
| 3.10 | README update + Phase 3 close-out | ✅ Complete |

### Agents

**InvestigationAgent** (`agents/investigation_agent.py`) — LangChain ReAct agent
with 5 tools: `get_hcp_risk_profile`, `get_rule_flags`, `get_peer_benchmark`,
`get_top_anomalous_features`, `search_policy_docs`. Returns a structured
`InvestigationReport` with policy citations, tier, SHAP-like feature drivers,
and recommended actions.

**MonitoringAgent** (`agents/monitoring_agent.py`) — 4 tools: `get_population_summary`,
`get_flagged_hcps`, `get_benchmark_outliers`, `get_top_risk_hcps`. Returns a
`MonitoringReport` with population-level risk distribution, trending alerts, and
cohort-level findings.

**PolicyAgent** (`agents/policy_agent.py`) — 2 tools: `search_policy_docs` (Qdrant
semantic search, 128 chunks), `lookup_rule` (keyword search over `rules.json`).
Returns a `PolicyAnswer` with rule thresholds, Nova vs PhRMA comparisons, and
chunk-level citations.

### Testing

- **80 pytest tests** across Phase 2 + Phase 3 (50 anomaly + 30 API)
- 0.90s total — CI-safe, no agent calls in default suite
- Agent tests isolated under `-m agent` marker

---

## Phase 4 — Compliance Risk Dashboard (Streamlit) ✅

### Overview

A 5-page Streamlit dashboard that consumes the FastAPI backend and presents
compliance risk data visually to compliance officers.

### Pages

1. **Compliance Risk Overview** — Population KPIs, risk tier distribution, trend
   analysis, choropleth map, MonitoringAgent on-demand analysis
2. **Rep–HCP Network** — Interactive pyvis network graph, 291 critical HCPs default,
   tier toggle, top 10 riskiest table
3. **HCP Explorer** — Paginated table of 97,011 HCPs, tier filter cards, risk score
   bars, click-to-navigate to HCP Detail
4. **HCP Detail** — Risk score gauge, rule flags with policy citations, peer benchmark,
   InvestigationAgent LLM report
5. **Policy Q&A** — RAG-powered Q&A over 5 policy documents, Nova Pharma vs PhRMA
   comparison, citation display

### Stack additions

- streamlit>=1.36.0
- plotly>=5.22.0
- streamlit-agraph>=0.0.45
- pydeck>=0.9.0
- httpx>=0.27.0

### Running locally

```bash
# Start backend
export $(grep -v '^#' docker/.env | xargs)
uvicorn api.main:app --host 127.0.0.1 --port 8001 --reload

# Start Streamlit (separate terminal)
python3 -m streamlit run streamlit_app/app.py
```

### Running with Docker

```bash
docker compose -f docker/docker-compose.yml up -d
# API:       http://localhost:8000
# Streamlit: http://localhost:8502
# MLflow:    http://localhost:5001
```

### Known limitations (dev environment)

- State/specialty fields NULL — choropleth and specialty charts show graceful fallbacks
- Rep–HCP edges unavailable — `rep_id` not in API response
- Peer benchmarks use normalized scores — Athena required for dollar amounts
- SHAP values served from investigation report, not HCP profile

### Post-Phase 4 backlog

- Hierarchical rep → HCP drill-down (requires `rep_id` Athena fix)
- Top flag column on HCP Explorer (requires per-HCP flags endpoint)
- State/specialty charts after Athena re-run
- Model comparison (IF vs LOF vs OCSVM)
- LangGraph supervisor agent
- Medium article: "From ML to AI Agents: Building a Pharma Compliance Platform in 2025"

---

## Repository Structure

```
compliance-risk-investigator/
├── agents/
│   ├── investigation_agent.py
│   ├── monitoring_agent.py
│   ├── policy_agent.py
│   ├── schemas.py
│   └── tools/
│       ├── investigation_tools.py
│       └── policy_tools.py
├── api/
│   ├── main.py
│   ├── dependencies.py
│   ├── test_api.py
│   └── routers/
│       ├── hcps.py
│       ├── events.py
│       ├── monitoring.py
│       ├── policy.py
│       └── benchmarks.py
├── streamlit_app/
│   ├── app.py
│   ├── config.py
│   ├── requirements.txt
│   ├── components/
│   │   └── api_client.py
│   └── pages/
│       ├── 1_Compliance_Risk_Overview.py
│       ├── 2_Rep_HCP_Network.py
│       ├── 3_HCP_Explorer.py
│       ├── 4_HCP_Detail.py
│       └── 5_Policy_QA.py
├── docker/
│   ├── Dockerfile
│   ├── Dockerfile.streamlit
│   ├── docker-compose.yml
│   ├── .env.example
│   └── README.md
├── pipelines/
│   ├── ingest/
│   ├── embed_policy_docs.py
│   ├── business_rules_registry.py
│   └── dbt_project/
├── features/
│   ├── feature_store.py
│   ├── industry_benchmarks.py
│   └── outputs/
├── models/
│   ├── isolation_forest.py
│   ├── scorer.py
│   └── outputs/
├── compliance/
│   └── rules.json
├── tests/
│   ├── test_anomaly_models.py
│   └── test_api.py (30 API tests)
└── docs/
    └── implementation/
        ├── phase1_implementation.md
        ├── phase2_implementation.md
        └── phase3_implementation.md
```

---

## How to Run

### Phase 2 (anomaly detection only)

```bash
source venv/bin/activate

# Infrastructure
cd docker && docker-compose up -d

# Policy grounding (run once)
python pipelines/embed_policy_docs.py
python pipelines/business_rules_registry.py

# Feature engineering
python features/hcp_spend_features.py
python features/event_features.py
python features/feature_store.py

# Anomaly detection
python models/rule_based_flags.py
python models/isolation_forest.py
python models/scorer.py

# Tests
pytest tests/test_anomaly_models.py -v
```

### Phase 3 (API + agents)

```bash
# Generate all parquets first (see Phase 2 above)
python features/industry_benchmarks.py
python pipelines/embed_policy_docs.py   # populate Qdrant

# Start API
export OPENAI_API_KEY=sk-...
uvicorn api.main:app --reload --port 8000

# Tests (no agent calls)
pytest tests/ -m "not agent" -v

# Example: policy Q&A
curl -X POST http://localhost:8000/policy/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the meal expense limit?"}'
```

### Phase 4 (Streamlit dashboard)

```bash
# Backend must be running first (see Phase 3 above)
python3 -m streamlit run streamlit_app/app.py
# Dashboard: http://localhost:8501
```

### Docker (full stack)

```bash
cd docker
cp .env.example .env    # add OPENAI_API_KEY + AWS credentials
docker-compose up --build
# API:       http://localhost:8000/docs
# Streamlit: http://localhost:8502
# MLflow:    http://localhost:5002
# Qdrant:    http://localhost:6333/dashboard
```

---

## Key Design Decisions

- **Deterministic decisions, LLM narratives only** — risk scores, tier assignments,
  and rule flags are fully deterministic; LLMs only generate natural-language
  summaries and citations, never numeric decisions
- **Rule-based + Isolation Forest dual detection** — rules provide policy traceability;
  IF catches anomalous patterns not covered by explicit rules; unified scorer blends
  both (60% rule, 40% IF)
- **Per-HCP SHAP explanations, not global feature importance** — each investigation
  report explains which features drove *that specific HCP's* score, not a global average
- **Policy grounding via Qdrant RAG (128 chunks, 5 docs)** — every agent answer is
  grounded in specific policy chunks with chunk_id citations; thresholds sourced
  from `rules.json`, never invented by the LLM
- **MLflow audit trail on every agent invocation** — latency, confidence, chunk
  counts, and rule matches logged per run for compliance auditability
- **Zero DB writes from any agent (read-only parquets)** — agents read from parquet
  files only; no agent can modify risk scores or flags
- **Violations organic in synthetic data, not seeded** — violation flags emerge from
  threshold breaches in the synthetic generator, not injected post-hoc
- **Ground truth separated from feature matrix** — violation labels never enter the
  ML pipeline; enforced at the dbt layer

---

## Known Limitations

### Phase 2
- Cap breach flags elevated in synthetic data distribution (~37% of HCPs) — CMS
  spend values not scaled to realistic annual cap thresholds
- `recall_high_or_critical` at 0.41 vs 0.70 long-term target — synthetic violation
  labels have limited correlation with rule flags on dev data
- Athena/DuckDB feature split resolved at Python layer only; dbt models do not yet
  abstract the target difference

### Phase 3
- `specialty=None` for all HCPs in dev environment — Athena-backed specialty lookup
  not active locally; peer benchmarks use aggregate population instead of specialty cohort
- Industry benchmarks require Athena connection; EPS (100-point) fallback active in dev
- No train/test temporal split yet (Task 3.6 backlog) — model trained on full dataset
- Agent cold-start latency ~30s on first request — LangChain agent executor
  initialization is deferred to first call to avoid Docker startup hang
- Embedding model mismatch: `embed_policy_docs.py` uses `text-embedding-ada-002`
  but `policy_tools.py` queries with `text-embedding-3-small` — relevance scores
  from `search_policy_docs` are near-random; answers remain correct via `lookup_rule`

### Phase 4
- `state` and `specialty` NULL in dev — choropleth map and specialty bar chart
  show graceful "data unavailable" fallbacks; will populate after Athena re-run
- Rep–HCP edges absent — `rep_id` not returned by `/hcps` endpoint; network page
  shows HCP-only graph with rep panel stub
- Peer benchmark values are RobustScaler-normalized scores, not dollar amounts —
  Athena connection required for raw spend figures
- Network node clicks use streamlit-agraph — clicked node ID returned natively,
  no iframe boundary; rep edges absent until `rep_id` schema fix

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Data warehouse | AWS Athena + S3 + Glue |
| Feature store | dbt + DuckDB |
| ML models | scikit-learn (Isolation Forest) + SHAP |
| Vector store | Qdrant |
| AI agents | LangChain + OpenAI GPT-4o-mini |
| API | FastAPI + uvicorn |
| Experiment tracking | MLflow 3.10.1 |
| UI | Streamlit (Phase 4) |
| Containerisation | Docker + docker-compose |

---

> Built for Nova Pharma Inc. (pseudonym for Takeda Pharmaceutical)
