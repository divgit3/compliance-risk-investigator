![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Phase 1](https://img.shields.io/badge/Phase%201-Complete-brightgreen)
![Phase 2](https://img.shields.io/badge/Phase%202-Complete-brightgreen)

# Compliance Risk Investigator AI

> Pharma compliance analytics platform for Nova Pharma Inc
> Anomaly detection · Policy grounding · HCP risk scoring

A production-style compliance analytics platform that detects anomalies in
pharmaceutical HCP interactions using real CMS Open Payments data and synthetic
internal compliance records. Ingests 18GB+ of public data, transforms it via dbt,
scores 97K HCPs with a rule-based + Isolation Forest dual detection engine, and
grounds all flags in policy documents via RAG (LangChain + OpenAI + Qdrant).
Findings surface through an AI copilot UI in Streamlit, backed by a FastAPI service
layer.

> **Data Notice:** All HCP identities are pseudonymized. Nova Pharma Inc is a
> fictional company (based on Takeda publicly reported data). No real proprietary
> data is used. CMS Open Payments data is public, sourced from
> [cms.gov](https://www.cms.gov/priorities/innovation/data-and-reports/2023/openpayments-data).

---

## Architecture

See `docs/architecture/phase1_architecture.svg` for the full system diagram.

```
┌─────────────────────────────────────────────────────────────────────┐
│  INGEST LAYER                                                       │
│  CMS Open Payments CSVs (18GB) ──► S3 ──► Glue Catalog ──► Athena  │
│  Synthetic HCP Data (1.1M rows) ──► S3 (Parquet)                   │
│  Policy PDFs (5 docs) ──► S3 ──► chunks JSON ──► Qdrant (128 vecs) │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  TRANSFORM LAYER (dbt)                                              │
│  Staging: stg_cms_general_payments, stg_synthetic_*                 │
│  Phase 1 Marts: mart_target_payments · mart_competitor_payments     │
│                 mart_population_payments · mart_violation_gt        │
│  Phase 2 Marts: mart_hcp_spend_features · mart_event_features       │
│                 mart_hcp_risk_profile · mart_benchmark              │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  FEATURE LAYER (Phase 2)                                            │
│  hcp_spend_features.py · event_features.py · feature_store.py      │
│  104 features · 97,011 HCPs · ground truth separated               │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  ML LAYER (Phase 2)                                                 │
│  rule_based_flags.py (23 policy-traceable flags)                    │
│  isolation_forest.py (200 trees · 9,701 outliers)                   │
│  scorer.py (unified 0–100 risk score · 4 tiers)                     │
│  mlflow_tracking.py (experiment tracking · GT recall metrics)       │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  RAG + EXPLANATION LAYER (Phase 3)                                  │
│  Qdrant (policy embeddings) + LangChain + OpenAI ──► explanations  │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  API + UI LAYER (Phase 3/4)                                         │
│  FastAPI service ──► Streamlit AI copilot UI                        │
└─────────────────────────────────────────────────────────────────────┘
```

**Stack:** Python 3.12 · dbt 1.8.3 · DuckDB · AWS S3/Glue/Athena · Qdrant 1.9.4
· MLflow 3.10.1 · scikit-learn · OpenAI · LangChain · FastAPI · Streamlit · Docker

---

## Project Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | ✅ Complete | Data foundation |
| Phase 2 | ✅ Complete | Anomaly detection engine |
| Phase 3 | 🔜 Planned | AI agents + FastAPI |
| Phase 4 | 🔜 Planned | Streamlit UI |

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

## Phase 3 — AI Agents + FastAPI 🔜

- Investigation Agent — drill into flagged HCPs with policy citations
- Monitoring Agent — real-time spend threshold alerting
- Policy Agent — Qdrant RAG for compliance Q&A
- FastAPI backend serving risk scores and explanations

---

## Phase 4 — Streamlit UI 🔜

- 5-level drill-down UI (portfolio → HCP → interaction → event → policy)
- Rep→HCP network diagram
- Year-by-year benchmark charts
- Engagement quadrant visualization
- Policy citation viewer

---

## Repository Structure

```
compliance-risk-investigator/
├── pipelines/
│   ├── ingest/
│   │   ├── cms_downloader.py
│   │   ├── glue_crawler.py
│   │   ├── policy_doc_loader.py
│   │   └── synthetic_generator.py
│   ├── embed_policy_docs.py
│   ├── business_rules_registry.py
│   └── dbt_project/
│       └── models/
│           ├── staging/
│           └── marts/
├── features/
│   ├── hcp_spend_features.py
│   ├── event_features.py
│   ├── feature_store.py
│   └── outputs/
├── models/
│   ├── rule_based_flags.py
│   ├── isolation_forest.py
│   ├── scorer.py
│   ├── mlflow_tracking.py
│   └── outputs/
├── compliance/
│   └── rules.json
├── tests/
│   └── test_anomaly_models.py
├── notebooks/
│   ├── phase2_eda.ipynb
│   └── figures/
├── docs/
│   └── implementation/
│       ├── phase1_implementation.md
│       └── phase2_implementation.md
└── infrastructure/
    ├── docker/
    └── terraform/
```

---

## How to Run Phase 2

**Prerequisites:** Python 3.12, AWS credentials in `.env`, Qdrant and MLflow running

```bash
# Start infrastructure
docker compose up -d
# Qdrant UI: http://localhost:6333/dashboard
# MLflow UI: http://localhost:5001

source venv/bin/activate

# 1. Policy grounding (run once)
python pipelines/embed_policy_docs.py
python pipelines/business_rules_registry.py

# 2. dbt feature marts
cd pipelines/dbt_project
dbt run --select mart_hcp_spend_features
dbt run --select mart_event_features
dbt run --select mart_hcp_risk_profile
dbt run --select mart_benchmark

# 3. Python feature engineering
cd ../..
python features/hcp_spend_features.py
python features/event_features.py
python features/feature_store.py

# 4. Anomaly detection
python models/rule_based_flags.py
python models/isolation_forest.py
python models/scorer.py
python models/mlflow_tracking.py

# 5. Tests
pytest tests/test_anomaly_models.py -v

# 6. EDA notebook
jupyter notebook notebooks/phase2_eda.ipynb
```

---

## Key Design Decisions

- **Violations organic in synthetic data, not seeded** — violation flags emerge from
  threshold breaches in the synthetic generator, not injected post-hoc
- **Ground truth separated from feature matrix** — violation labels never enter the
  ML pipeline; enforced at the dbt layer
- **Business rules sourced from policy docs via RAG** — not hardcoded; every rule is
  traceable to a specific policy chunk with citation
- **Nova Pharma policy stricter than PhRMA** — 8 override rules with tighter
  thresholds reflect realistic internal compliance posture
- **Annual benchmarking, not lifetime** — OIG/CMS operate on program year basis;
  benchmarks computed per year
- **Rule-based + ML dual approach** — rules provide explainability; IF catches
  patterns rules miss; unified scorer blends both (60/40)
- **Athena/DuckDB split resolved at Python layer** — dbt runs against both targets;
  Python feature scripts handle dev/prod branching transparently

---

## Known Limitations (Phase 2)

- Cap breach flags elevated in synthetic data distribution (~37% of HCPs) — CMS
  spend values not scaled to realistic annual cap thresholds
- `recall_high_or_critical` at 0.41 vs 0.70 long-term target — synthetic violation
  labels have limited correlation with rule flags on dev data
- Industry specialty benchmarks incomplete on dev (Athena-only feature); engagement
  quadrant defaults to `'continue'` for all HCPs until Athena is connected
- Athena/DuckDB feature split resolved at Python layer only; dbt models do not yet
  abstract the target difference

---

## Tech Stack

| Layer | Tools |
|-------|-------|
| Data ingestion | Python 3.12 · Requests · Boto3 · Faker |
| Cloud storage | AWS S3 · Glue Data Catalog · Athena |
| Data transformation | dbt 1.8.3 · dbt-duckdb · dbt-athena · DuckDB |
| Vector database | Qdrant 1.9.4 |
| RAG / LLM | LangChain · OpenAI (embeddings + extraction) |
| Feature engineering | pandas · numpy · pyathena |
| Anomaly detection | scikit-learn (IsolationForest) |
| Experiment tracking | MLflow 3.10.1 |
| Visualization | matplotlib · seaborn · plotly |
| Testing | pytest (50 tests · 0.67s) |
| API layer | FastAPI · Uvicorn · Pydantic |
| UI | Streamlit |
| Containerisation | Docker · Docker Compose |
| Infrastructure | Terraform (Phase 3) |
