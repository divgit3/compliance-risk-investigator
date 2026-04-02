![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Phase 1](https://img.shields.io/badge/Phase%201-Complete-brightgreen)

# Compliance Risk Investigator AI

A production-style compliance analytics platform that detects anomalies in
pharmaceutical HCP interactions using real CMS Open Payments data and synthetic
internal compliance records. Ingests 18GB+ of public data, transforms it via dbt,
scores interactions with ML anomaly detection, and explains flagged cases through a
policy-grounded RAG pipeline (LangChain + OpenAI + Qdrant). Findings surface through
an AI copilot UI in Streamlit, backed by a FastAPI service layer.

> **Data Notice:** All HCP identities are pseudonymized. Nova Pharma Inc is a
> fictional company (based on Takeda publicly reported data). No real proprietary
> data is used. CMS Open Payments data is public, sourced from
> [cms.gov](https://www.cms.gov/priorities/innovation/data-and-reports/2023/openpayments-data).

---

## What It Does

1. **Ingests** real CMS Open Payments CSVs (2022-2024) into S3, catalogs them via
   AWS Glue, and makes them queryable in Athena
2. **Generates** 1.1M+ synthetic internal HCP interaction records across 97K fictional
   healthcare professionals with 14 violation types labeled
3. **Transforms** all data through a dbt pipeline (11 models, 51 tests) producing
   feature marts for anomaly detection
4. **Stores** 171 policy document chunks (PhRMA, OIG, CMS, internal) in Qdrant for
   RAG-based compliance explanation
5. **Detects** (Phase 2) anomalous patterns using scikit-learn, tracked in MLflow
6. **Explains** (Phase 2) flagged interactions using LangChain + OpenAI grounded in
   policy documents

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  INGEST LAYER                                                       │
│  CMS Open Payments CSVs (18GB) ──► S3 ──► Glue Catalog ──► Athena  │
│  Synthetic HCP Data (1.1M rows) ──► S3 (Parquet)                   │
│  Policy PDFs (5 docs) ──► S3 ──► chunks JSON ──► Qdrant (171 vecs) │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  TRANSFORM LAYER (dbt)                                              │
│  Staging: stg_cms_general_payments, stg_synthetic_*                 │
│  Marts:   mart_target_payments (473K)                               │
│           mart_competitor_payments (4.3M)                           │
│           mart_population_payments (13.2M)                          │
│           mart_hcp_interactions_features                            │
│           mart_speaker_events_features                              │
│           mart_violation_ground_truth (labels)                      │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  ML LAYER (Phase 2)                                                 │
│  scikit-learn anomaly detection ──► MLflow experiment tracking      │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  RAG + EXPLANATION LAYER (Phase 2)                                  │
│  Qdrant (policy embeddings) + LangChain + OpenAI ──► explanations  │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│  API + UI LAYER (Phase 3)                                           │
│  FastAPI service ──► Streamlit AI copilot UI                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Tools |
|---|---|
| Data ingestion | Python, Requests, Boto3, Faker |
| Cloud storage | AWS S3, Glue Data Catalog, Athena |
| Data transformation | dbt-core, dbt-duckdb, dbt-athena, DuckDB |
| Vector database | Qdrant v1.9.4 |
| RAG / LLM | LangChain, LangChain-OpenAI, OpenAI API |
| PDF parsing | pdfplumber, fpdf2 |
| Anomaly detection | scikit-learn |
| Experiment tracking | MLflow v2.14.1 |
| API layer | FastAPI, Uvicorn, Pydantic |
| UI | Streamlit |
| Containerisation | Docker, Docker Compose |
| Infrastructure | Terraform (Phase 3) |

---

## Data

### Real Data (CMS Open Payments 2022-2024)
- **18.4GB** of raw CSV files in S3 (`raw/cms_open_payments/`)
- ~18M physician payment records across 3 years
- Anchor company: Takeda (pseudonymized as Nova Pharma Inc)
- 4 competitors: Janssen, Merck, Amgen, BMS (pseudonymized)
- **473K** Nova Pharma payments | **4.3M** competitor | **13.2M** population

### Synthetic Data (Nova Pharma Internal Records)
- **97K** fictional HCPs in `hcp_master.parquet`
- **1M+** HCP interaction records with 14 violation types labeled
- Speaker program events + attendee records
- All stored in S3 (`synthetic/`) as Parquet

### Policy Documents (5 docs, 171 chunks)
| Document | Source | Chunks |
|---|---|---|
| PhRMA Code on Interactions with HCPs (2022) | Public | ~45 |
| OIG CPG: Pharmaceutical Manufacturers (2003) | Public | ~68 |
| OIG Special Fraud Alert: Speaker Programs (2020) | Public | ~12 |
| CMS Open Payments Data Dictionary | Public | ~44 |
| Nova Pharma Internal Compliance Policy | Synthetic | ~2 |

Stored in S3 (`raw/policy_docs/`) and indexed in Qdrant (`policy_docs` collection,
1536-dim, Cosine similarity).

---

## Phase 1 Status

| Component | Status | Details |
|---|---|---|
| CMS data pipeline | ✅ Complete | 18.4GB in S3, Glue cataloged, Athena queryable |
| Synthetic data | ✅ Complete | 1.1M+ records, 97K HCPs, 14 violation types |
| dbt models | ✅ Complete | 11 models, 51 tests passing (Athena + DuckDB) |
| Policy docs | ✅ Complete | 5 docs, 171 chunks in S3 + Qdrant |
| Qdrant | ✅ Running | localhost:6333, `policy_docs` collection ready |
| MLflow | ✅ Running | localhost:5001, tracking server ready |

---

## How to Run Locally

**Prerequisites:** Python 3.12, Docker Desktop, AWS credentials with S3/Glue/Athena access

```bash
# 1. Clone the repo
git clone <repo-url>
cd compliance-risk-investigator

# 2. Set up Python environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# Edit .env: fill in AWS credentials and OPENAI_API_KEY

# 4. Start local infrastructure (Qdrant + MLflow)
docker compose up -d
# Qdrant UI:  http://localhost:6333/dashboard
# MLflow UI:  http://localhost:5001

# 5. Run dbt (DuckDB, synthetic data — no AWS needed)
cd pipelines/dbt_project
dbt run

# 6. Run dbt against Athena (requires AWS credentials)
dbt run --target athena

# 7. Ingest policy documents (requires AWS + PyPI)
python pipelines/ingest/policy_doc_loader.py
```

### Re-crawl CMS data (if needed)
```bash
# Requires GLUE_ROLE_ARN in .env
python pipelines/ingest/glue_crawler.py
```

---

## Project Structure

```
compliance-risk-investigator/
├── README.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── requirements.txt
├── pipelines/
│   ├── ingest/
│   │   ├── cms_downloader.py        # Downloads CMS CSVs → S3
│   │   ├── glue_crawler.py          # Glue catalog + OpenCSVSerde patch
│   │   ├── policy_doc_loader.py     # PDF download/chunk/upload + Qdrant index
│   │   └── synthetic_generator.py   # Generates 1.1M synthetic records
│   └── dbt_project/
│       ├── dbt_project.yml
│       ├── profiles.yml             # dev=DuckDB, athena=Athena
│       ├── seeds/
│       │   └── company_mapping.csv  # Pseudonym mappings (DuckDB target)
│       └── models/
│           ├── staging/
│           │   ├── sources.yml
│           │   ├── staging.yml
│           │   ├── stg_cms_general_payments.sql
│           │   ├── stg_synthetic_interactions.sql
│           │   └── stg_synthetic_speaker_programs.sql
│           └── marts/
│               ├── marts.yml
│               ├── mart_target_payments.sql
│               ├── mart_competitor_payments.sql
│               ├── mart_population_payments.sql
│               ├── mart_hcp_interactions_features.sql
│               ├── mart_speaker_events_features.sql
│               ├── mart_attendees_features.sql
│               └── mart_violation_ground_truth.sql
├── features/                        # Phase 2: feature engineering
├── models/                          # Phase 2: ML models
├── rag/                             # Phase 2: RAG pipeline
├── api/                             # Phase 3: FastAPI service
├── ui/                              # Phase 3: Streamlit UI
├── infrastructure/
│   ├── docker/
│   └── terraform/
├── notebooks/
└── tests/
```
