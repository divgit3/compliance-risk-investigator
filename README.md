![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)

# Compliance Risk Investigator AI

A production-style data + AI portfolio project that detects compliance anomalies
in pharmaceutical Healthcare Professional (HCP) interactions. It ingests public
CMS Open Payments data alongside synthetic internal records for the fictional company
NovaPharma Inc, runs SQL-based transformations via dbt, scores interactions using
machine learning anomaly detection, and explains flagged cases using a
policy-grounded RAG pipeline (LangChain + OpenAI + Qdrant). Findings are surfaced
through an AI copilot UI built in Streamlit, backed by a FastAPI service layer.

---

## Tech Stack

| Layer | Tools |
|---|---|
| Data ingestion | Python, Requests, Faker |
| Data transformation | dbt-core, dbt-duckdb, DuckDB |
| Vector database | Qdrant |
| RAG / LLM | LangChain, LangChain-OpenAI, OpenAI API |
| PDF parsing | PyPDF2 |
| Anomaly detection | scikit-learn |
| Experiment tracking | MLflow |
| API layer | FastAPI, Uvicorn, Pydantic |
| UI | Streamlit |
| Containerisation | Docker, docker-compose |
| Cloud (later phases) | AWS S3, Athena, Glue, ECR, ECS |
| Infrastructure | Terraform |

---

## Project Structure

```
compliance-risk-investigator/
├── README.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── requirements.txt
├── infrastructure/
│   ├── docker/
│   │   ├── Dockerfile.api
│   │   ├── Dockerfile.ui
│   │   └── Dockerfile.pipeline
│   └── terraform/
├── data/
│   ├── raw/
│   │   ├── cms_open_payments/
│   │   └── policy_docs/
│   ├── synthetic/
│   │   ├── hcp_interactions/
│   │   ├── speaker_programs/
│   │   └── anomaly_cases/
│   └── processed/
├── pipelines/
│   ├── ingest/
│   │   ├── cms_downloader.py
│   │   ├── policy_doc_loader.py
│   │   └── synthetic_generator.py
│   └── dbt_project/
│       ├── models/
│       │   ├── staging/
│       │   ├── intermediate/
│       │   └── marts/
│       ├── tests/
│       └── dbt_project.yml
├── features/
├── models/
├── rag/
├── ai/
│   └── prompts/
├── api/
├── ui/
│   └── pages/
├── notebooks/
├── tests/
└── docs/
```

---

## How to Run Locally

> Full setup instructions will be added as each phase is completed.

**Prerequisites:** Python 3.12, Docker Desktop

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
# Edit .env and fill in your OPENAI_API_KEY

# 4. Start infrastructure services
docker compose up -d

# 5. Run data pipeline (coming in later tasks)
# 6. Launch API (coming in later tasks)
# 7. Launch UI (coming in later tasks)
```

---

## Architecture

> Detailed architecture diagram and data flow description will be added in a later phase.

**High-level flow:**

```
CMS Open Payments (public)  ──┐
                               ├── Ingest → dbt (DuckDB) → Feature Engineering
Synthetic HCP Data (NovaPharma)┘
                                         ↓
                               Anomaly Detection (scikit-learn / MLflow)
                                         ↓
                    Policy Docs (PDF) → RAG (Qdrant + LangChain + OpenAI)
                                         ↓
                               FastAPI → Streamlit Copilot UI
```

---

> **Data Notice:** All HCP identities are pseudonymized. NovaPharma Inc is a
> fictional company. No real proprietary data is used. CMS Open Payments data
> is public and sourced from [cms.gov](https://www.cms.gov/priorities/innovation/data-and-reports/2023/openpayments-data).
