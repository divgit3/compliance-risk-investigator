![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Phase 1](https://img.shields.io/badge/Phase%201-Complete-brightgreen)
![Phase 2](https://img.shields.io/badge/Phase%202-Complete-brightgreen)
![Phase 3](https://img.shields.io/badge/Phase%203-Complete-brightgreen)
![Phase 4](https://img.shields.io/badge/Phase%204-Complete-brightgreen)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

# Compliance Risk Investigator AI

> Pharma HCP compliance analytics platform — anomaly detection · AI agents · policy grounding · FastAPI backend

A production-style compliance analytics platform that detects anomalies in pharmaceutical HCP interactions using real CMS Open Payments data and synthetic internal records. Scores 97K HCPs with a rule-based + Isolation Forest dual detection engine, grounds all flags in policy documents via RAG, and serves structured compliance reports through a FastAPI backend backed by three LangChain AI agents.

> **Data Notice:** All HCP identities are pseudonymized. Nova Pharma Inc is a fictional company (based on Takeda publicly reported data). No real proprietary data is used. CMS Open Payments data is public, sourced from [cms.gov](https://www.cms.gov/priorities/innovation/data-and-reports/2023/openpayments-data).

---

## Built by

Built by Divya Rajaraman as a portfolio piece — regulated-industry engineering practices applied to pharma HCP compliance in a modern AI agent stack.

[LinkedIn](https://www.linkedin.com/in/divyarajaraman)

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

**Stack:** Python 3.12 · dbt 1.8.3 · DuckDB · AWS S3/Glue/Athena · Qdrant · MLflow 3.10.1 · scikit-learn · SHAP · OpenAI GPT-4o-mini · LangChain · FastAPI · Streamlit · Docker

---

## What It Does

**Data foundation.** 18.4GB of CMS Open Payments data (2022–2024, ~18M physician payment records) combined with 1.1M+ synthetic HCP interaction records across 97K HCPs. dbt transforms raw data into four feature marts; a Python feature store assembles a 104-feature matrix per HCP.

**Anomaly detection.** Dual-detection engine: 23 policy-traceable rule flags plus a 200-tree Isolation Forest. A unified scorer blends both (60% rule, 40% IF) into a 0–100 risk score assigned to four tiers (Critical / High / Medium / Low). SHAP values explain per-HCP score drivers in every investigation report.

**Policy grounding.** Business rules are extracted from policy documents via RAG — not hardcoded — ensuring every flag is traceable to a specific policy chunk. 24 rules across 5 documents (PhRMA Code 2022, OIG CPG, OIG Speaker Fraud Alert, CMS Data Dictionary, Nova Pharma Internal Policy). 8 Nova Pharma overrides are stricter than the PhRMA baseline; thresholds (meal limit $25/$50/$100, speaker FMV $3,500, annual cap $75,000) stored in `compliance/rules.json` with full citation provenance.

**AI agents.** Three LangChain agents on OpenAI GPT-4o-mini: **InvestigationAgent** produces per-HCP compliance reports with policy citations and SHAP feature drivers; **MonitoringAgent** runs population-level risk analysis and cohort trending; **PolicyAgent** answers natural-language compliance questions via Qdrant RAG with Nova Pharma vs. PhRMA comparisons and chunk-level citations.

**API + dashboard.** Nine FastAPI endpoints expose risk profiles, rule flags, agent reports, and benchmarks. A 5-page Streamlit dashboard presents compliance risk visually: population KPIs, an interactive rep–HCP network graph, paginated HCP explorer, per-HCP drill-down with SHAP explanations, and a RAG-powered Policy Q&A panel.

<!-- TODO: add dashboard screenshot -->

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/hcps` | List HCPs sorted by risk score |
| GET | `/hcps/{hcp_id}` | Risk profile for a single HCP |
| GET | `/hcps/{hcp_id}/investigate` | InvestigationAgent — full compliance report |
| GET | `/hcps/{hcp_id}/flags` | Rule flags that fired for a single HCP |
| GET | `/events` | Event feature aggregates per HCP |
| GET | `/events/{hcp_id}` | Event features for a single HCP |
| GET | `/monitoring` | MonitoringAgent — population-level risk analysis |
| GET | `/benchmarks/{hcp_id}` | Industry benchmark comparison |
| POST | `/policy/query` | PolicyAgent RAG — natural language compliance Q&A |

Agent endpoints are async and call OpenAI — expect 5–30s latency. Non-agent endpoints return in <100ms.

---

## Reliability & Evaluation

The 1.2 arc focused on the engineering that distinguishes a working demo from a system you'd trust in a compliance workflow.

**Sentence-level citation highlighting.** The Policy Q&A panel maps agent answers back to specific sentences in source PDF documents. A coordinate pipeline extracts line-level bounding boxes during embedding; the renderer applies a two-stage approach — citation chunk display plus sentence-level highlight overlay. A score-threshold backstop handles TOPIC ABSENT cases where no high-confidence sentence match exists, preventing spurious highlights.

<!-- TODO: add policy Q&A screenshot with sentence highlighting -->

**Retrieval ranking debugging.** Discovered that LangChain agent query reformulation was inverting retrieval ranking on specifically-phrased questions — paraphrasing loses the discriminating vocabulary in the original question. Diagnosed the failure through LangChain's async executor boundaries (a ContextVar-based fix passed unit tests but failed in production because the framework's tool runner doesn't inherit the caller's execution context). Addressed via prompt-layer instruction to pass user questions verbatim. Documented embedding dilution from synthetic-document boilerplate as a separately-scoped follow-on rather than over-extending the fix.

**RAGAS evaluation framework.** 15 Q&A pairs across 7 compliance categories, evaluated against three judge metrics: Faithfulness (≥0.75), Response Relevancy (≥0.75), Context Precision (≥0.60). Offline replay mode re-runs evaluations against cached LLM responses for reproducibility; CI gates enforce thresholds; latency P95 tracked per run.

**Post-processor architecture.** `agents/post_processors/` provides output hardening before responses reach the user: an over-narration safety net for TOPIC ABSENT answers and a scope-mismatch detector that catches responses bleeding outside designated agent boundaries.

**Trustworthy AI evaluation framework.** 11-attribute rubric: Faithfulness, Retrieval Relevance, Groundedness, Graceful Failure, Auditability, Consistency, Latency, Robustness, Calibrated Confidence, Scope Adherence, Reproducibility. Full rubric and measurement methodology in [docs/operations.md](docs/operations.md).

Detailed engineering log — retrieval ranking debugging arc, RAGAS findings, and lessons across 1.2a–1.2g: [evaluation/policy_ragas/lessons_log.md](evaluation/policy_ragas/lessons_log.md)

---

## Key Design Decisions

- **Deterministic decisions, LLM narratives only** — risk scores, tier assignments, and rule flags are fully deterministic; LLMs only generate natural-language summaries and citations, never numeric decisions
- **Rule-based + Isolation Forest dual detection** — rules provide policy traceability; IF catches anomalous patterns not covered by explicit rules; unified scorer blends both (60% rule, 40% IF)
- **Per-HCP SHAP explanations, not global feature importance** — each investigation report explains which features drove *that specific HCP's* score, not a global average
- **Policy grounding via Qdrant RAG (128 chunks, 5 docs)** — every agent answer is grounded in specific policy chunks with chunk_id citations; thresholds sourced from `rules.json`, never invented by the LLM
- **MLflow audit trail on every agent invocation** — latency, confidence, chunk counts, and rule matches logged per run for compliance auditability
- **Zero DB writes from any agent (read-only parquets)** — agents read from parquet files only; no agent can modify risk scores or flags
- **Violations organic in synthetic data, not seeded** — violation flags emerge from threshold breaches in the synthetic generator, not injected post-hoc
- **Ground truth separated from feature matrix** — violation labels never enter the ML pipeline; enforced at the dbt layer

---

## Quick Start

**Prerequisites:** Python 3.12, Docker Desktop, AWS credentials, OpenAI API key

```bash
git clone <repo-url> && cd compliance-risk-investigator
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp docker/.env.example docker/.env   # fill in OPENAI_API_KEY and AWS credentials
cd docker && docker compose up -d
```

Services after startup: API `http://localhost:8000` · Streamlit `http://localhost:8502` · MLflow `http://localhost:5002` · Qdrant `http://localhost:6333`

> For full setup, daily startup scenarios, credential rotation, and operational details, see [docs/operations.md](docs/operations.md).

---

## Repository Structure

```
agents/         — Three LangChain agents (Investigation, Monitoring, Policy)
api/            — FastAPI backend, 9 endpoints
streamlit_app/  — 5-page Streamlit dashboard
pipelines/      — Data ingestion, dbt models, policy doc embedding
features/       — Feature engineering (104 features, 97K HCPs)
models/         — Anomaly detection (rule-based + Isolation Forest)
evaluation/     — RAGAS evaluation framework + lessons log
docs/           — Operations guide, implementation history
docker/         — Docker compose for full stack
scripts/        — Diagnostic and one-off scripts
tests/          — Unit and integration tests
```

---

## Scope & Known Limitations

- **Recall ceiling on dev data.** `recall_high_or_critical` is 0.41 vs. a 0.70 long-term target. The synthetic violation labels have limited correlation with rule flags on the generated distribution — a synthetic-data constraint, not a model capability issue.
- **Athena dev-mode fallback.** When Athena is offline, peer benchmarks substitute aggregate-population medians for specialty-cohort medians. Dollar-amount benchmark comparisons require a live Athena connection.
- **No temporal train/test split.** The Isolation Forest is trained on the full dataset; temporal holdout remains on the backlog.
- **Agent cold-start latency ~30s.** LangChain agent executor initialization is deferred to first request to avoid Docker startup hangs; subsequent calls are faster.
- **Retrieval ranking on low-specificity questions.** Questions without strong company-specific vocabulary can surface chunks with dense topical text that outranks chunks containing the actual answer. Root cause is embedding dilution from synthetic-document boilerplate. Diagnosis and scope documented in [evaluation/policy_ragas/lessons_log.md](evaluation/policy_ragas/lessons_log.md).

---

## License

MIT — see [LICENSE](LICENSE).

---

Built by Divya Rajaraman · [LinkedIn](https://www.linkedin.com/in/divyarajaraman) · [License](LICENSE)
