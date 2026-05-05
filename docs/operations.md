# Operations Guide

Operational reference for running, debugging, and maintaining the Compliance Risk
Investigator stack. Complements the README Quick Start with deeper detail for users
running the stack locally or in containers.

---

## Table of Contents

- [Local development setup](#local-development-setup)
- [Daily startup scenarios](#daily-startup-scenarios)
- [Verification checklist](#verification-checklist)
- [Common gotchas](#common-gotchas)
- [Credential rotation](#credential-rotation)
- [Athena and S3 configuration](#athena-and-s3-configuration)
- [Trustworthy AI evaluation framework](#trustworthy-ai-evaluation-framework)

---

## Local development setup

Full setup sequence for a clean environment or after time away. Follow steps in
order — the pipeline steps have dependencies on earlier outputs.

### 1. Activate venv

```bash
source venv/bin/activate
```

### 2. Load environment variables for local scripts

`dbt` and the feature pipelines need `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`OPENAI_API_KEY`, and `ATHENA_S3_BUCKET` in the shell. The `.env` file lives under
`docker/` for Docker Compose, but local runs need them exported:

```bash
export $(grep -v '^#' docker/.env | xargs)

# Verify AWS credentials are valid (not expired)
echo $AWS_ACCESS_KEY_ID | head -c 8
aws sts get-caller-identity
```

If credentials are expired, regenerate from your AWS IAM console or SSO provider.

### 3. Rebuild DuckDB via dbt

The `data/processed/compliance.duckdb` symlink points to
`pipelines/dbt_project/compliance_risk.duckdb`. If that file is missing or empty,
regenerate it:

```bash
cd pipelines/dbt_project
dbt run
cd ../..
```

Expected: 10–11 models pass. 4 CMS-related models may fail with
`"Catalog awsdatacatalog does not exist"` — this is the known Athena/DuckDB
federation gap and is not blocking for the core pipeline.

Verify DuckDB has the required tables:

```bash
python -c "
import duckdb
con = duckdb.connect('pipelines/dbt_project/compliance_risk.duckdb', read_only=True)
for t in con.execute('SHOW TABLES').fetchall():
    name = t[0]
    count = con.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
    print(f'  {name}: {count} rows')
"
```

Minimum expected: `mart_hcp_risk_profile: 97011 rows`, plus
`mart_hcp_interactions_features`, `mart_violation_ground_truth`.

### 4. Run feature pipelines (order matters)

```bash
python features/feature_store.py
python models/isolation_forest.py
python features/industry_benchmarks.py
# python pipelines/embed_policy_docs.py  # skip if Qdrant already populated
```

Expected timings:
- `feature_store.py`: ~10 seconds
- `isolation_forest.py`: ~50 seconds (SHAP computation dominates — ~45s)
- `industry_benchmarks.py`: ~3 seconds (local fallback, no Athena required)

### 5. Start Docker stack

```bash
cd docker
docker compose up -d
cd ..
```

If containers were already running and you only refreshed data files:

```bash
cd docker
docker compose restart api streamlit
cd ..
```

### 6. Verify services

| Service | URL |
|---------|-----|
| Streamlit dashboard | http://localhost:8502 |
| FastAPI docs | http://localhost:8000/docs |
| MLflow | http://localhost:5002 |
| Qdrant dashboard | http://localhost:6335/dashboard |

### 7. Smoke test

```bash
# API health
curl -s http://localhost:8000/health

# HCP endpoint — critical for Rep-HCP Network page
curl -s "http://localhost:8000/hcps?tier=critical&limit=1" | python -m json.tool | grep rep_id
# Expected: "primary_rep_id": "REP_XXXX"
```

---

## Daily startup scenarios

### Scenario 1: Everything worked recently — just restart

```bash
source venv/bin/activate

# Start Docker stack (all 4 services)
cd docker && docker compose up -d && cd ..

# Wait ~30 seconds, then smoke-test
sleep 30
curl -s http://localhost:8000/health
curl -s "http://localhost:8000/hcps?tier=critical&limit=1" | python -m json.tool | grep rep_id

open http://localhost:8502
```

If smoke tests pass, ready to work. If not, use Scenario 2.

### Scenario 2: After time away or data looks stale

```bash
source venv/bin/activate

# 1. Load env vars for local scripts
export $(grep -v '^#' docker/.env | xargs)
aws sts get-caller-identity   # verify credentials are valid

# 2. Regenerate DuckDB if empty
cd pipelines/dbt_project && dbt run && cd ../..

# 3. Rerun data pipelines (order matters)
python features/feature_store.py
python models/isolation_forest.py
python features/industry_benchmarks.py

# 4. Bring up Docker stack
cd docker
docker compose down
docker compose up -d
cd ..

# 5. Smoke test (wait for API healthcheck)
sleep 30
curl -s http://localhost:8000/health
curl -s "http://localhost:8000/hcps?tier=critical&limit=1" | python -m json.tool | grep rep_id

open http://localhost:8502
```

### Scenario 3: After code changes to API or Streamlit

Code changes require a Docker image rebuild — `docker compose restart` alone does
not pick up code changes (see [Common gotchas](#common-gotchas)):

```bash
cd docker
docker compose build api          # or 'streamlit' for Streamlit changes
docker compose up -d --force-recreate api
cd ..
```

### Shutdown

```bash
cd docker
docker compose down
cd ..
```

Stops all containers cleanly. Data persists in named volumes. The next
`docker compose up -d` resumes from where it left off.

---

## Verification checklist

Quick visual check after startup — all of these should pass before starting work:

- [ ] `curl http://localhost:8000/health` returns `{"status":"ok"}`
- [ ] `curl "http://localhost:8000/hcps?tier=critical&limit=1"` includes `primary_rep_id` in response
- [ ] http://localhost:8502 loads without errors
- [ ] **Compliance Risk Overview** page shows ~291 critical HCPs
- [ ] **Rep-HCP Network** page shows red squares (reps), lines (edges), and leaderboard populated
- [ ] **HCP Explorer** page lists HCPs sortable by risk score
- [ ] **HCP Detail** page (pick any HCP) shows risk score, flags, features
- [ ] **Policy QA** page returns answers with citations for a simple question

If any of these fail, check which Scenario above applies and re-run from there.

---

## Common gotchas

### Docker image caching — code changes not picked up

`docker compose restart` and `--force-recreate` alone **do not** pick up code
changes. They reuse the existing image. Only `docker compose build` rebuilds with
current code.

See [docker/README.md — Rebuilding after agent code changes](../docker/README.md#rebuilding-after-agent-code-changes)
for the full rebuild procedure including the `--no-cache` flag (required on
external/network volumes where file timestamps may not change).

**Diagnosing a stale container** — check whether the running container has your
change before assuming a code bug:

```bash
docker exec docker-api-1 grep -A 4 'keyword_from_your_change' /app/api/routers/relevant_file.py
```

If the container shows old code but your local file has new code, the image needs
a rebuild.

### .env file and local scripts

The `.env` file lives under `docker/` for Docker Compose, but local scripts (`dbt`,
feature pipelines) need these variables in the shell environment. They are not
loaded automatically. Export them before running any local script:

```bash
export $(grep -v '^#' docker/.env | xargs)
```

Options for making this less manual: (a) symlink `docker/.env` to the repo root,
(b) wrap common commands in a shell script that exports first, or (c) use
`direnv` with an `.envrc` that sources the file on `cd`.

---

## Credential rotation

### AWS key rotation

AWS access keys must be updated in **two separate files**. Docker containers and
the host CLI run in separate environments and each reads credentials from a
different source:

- `~/.aws/credentials` — used by the host CLI (`aws ...` commands), host Python
  scripts, and `dbt` commands
- `docker/.env` — used by Docker containers (API agents, Streamlit, anything
  running inside `docker compose`)

**Procedure:**

1. AWS Console → IAM → Users → `<your-iam-username>` → Security credentials →
   "Create access key"
2. Save the new Access Key ID and Secret Access Key in a secure location
   (password manager, etc.)
3. Update `~/.aws/credentials`:
   ```bash
   nano ~/.aws/credentials   # or your preferred editor
   # Update aws_access_key_id and aws_secret_access_key
   ```
4. Update `docker/.env`:
   ```bash
   nano docker/.env
   # Update AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY
   ```
5. Verify both files have matching keys using the last-4-character suffix
   (safe — does not expose the key):
   ```bash
   grep "AKIA" ~/.aws/credentials | grep -oE "[A-Z0-9]{4}$"
   grep "AWS_ACCESS_KEY_ID" docker/.env | grep -oE "[A-Z0-9]{4}$"
   ```
   Both commands should print the same 4-char suffix.
6. Verify CLI auth: `aws sts get-caller-identity`
7. Restart Docker containers to load the new env:
   ```bash
   cd docker && docker compose down && docker compose up -d && cd ..
   ```
8. Verify the container picked up the new key (safe — only prints suffix):
   ```bash
   docker exec docker-api-1 sh -c 'echo "$AWS_ACCESS_KEY_ID" | grep -oE "[A-Z0-9]{4}$"'
   ```
9. Wipe dbt build artifacts that may embed the old key:
   ```bash
   rm -rf pipelines/dbt_project/target/ pipelines/dbt_project/logs/
   ```
10. AWS Console → IAM → deactivate the old key (don't delete for 24 hours in
    case rollback is needed)
11. After 24 hours with no issues, delete the old key

### OpenAI API key rotation

The OpenAI key lives in **one place only**: `docker/.env`. It is read by
containers via `os.environ.get("OPENAI_API_KEY")` in agent code.

**Procedure:**

1. platform.openai.com/api-keys → Revoke the old key
2. Create a new key and save it securely
3. Update `docker/.env` — replace the `OPENAI_API_KEY` line
4. Restart containers:
   ```bash
   cd docker && docker compose down && docker compose up -d && cd ..
   ```
5. Verify (counts the variable, does not print the value):
   ```bash
   docker exec docker-api-1 env | grep -c "^OPENAI_API_KEY"
   # Expected: 1
   ```

### Security rules

- **Never** paste full `.env` or `~/.aws/credentials` contents into chat tools,
  logs, screenshots, or commits
- **Never** commit `.env` files (already gitignored — confirm with `git status`
  after edits)
- **Never** export keys at the shell prompt — the command enters shell history
  and persists across sessions. Use the inline-env pattern instead:
  ```bash
  OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" docker/.env | cut -d= -f2-) python3 -m pytest tests/
  ```
- **Inspect `.env` safely** using redaction:
  ```bash
  cat docker/.env | sed -E 's/(KEY|SECRET|TOKEN|PASSWORD)=.+/\1=***REDACTED***/g'
  ```
- **Key suffix comparison** is the safe way to verify two files match without
  exposing the full key: `grep -oE "[A-Z0-9]{4}$"`
- dbt's `on-run-start` hook resolves env vars and writes them to `target/`.
  Always wipe `target/` after key rotation

---

## Athena and S3 configuration

### Environment variables

| Variable | Read by | Default | Purpose |
|----------|---------|---------|---------|
| `AWS_DEFAULT_REGION` | boto3, most AWS SDKs | `us-east-1` | AWS region for Glue/Athena API calls |
| `AWS_REGION` | Some SDKs (pyathena) | `us-east-1` | Same purpose — different SDKs read different vars; set both |
| `ATHENA_DATABASE` | `features/industry_benchmarks.py` | `compliance_risk_raw` | Glue Catalog database name |
| `ATHENA_S3_BUCKET` | `features/industry_benchmarks.py`, pipelines | `s3://your-s3-bucket-name/athena-query-output/` | Where Athena writes query result CSVs |
| `ATHENA_S3_STAGING_DIR` | `features/hcp_spend_features.py` (pyathena) | same as above | pyathena staging location — same bucket, same path |

### S3 bucket layout

All data lives in `s3://your-s3-bucket-name/` (region: `us-east-1`):

```
s3://your-s3-bucket-name/
├── raw/cms_open_payments/          ← source CMS data; Glue table: cms_open_payments
├── synthetic/                      ← generated synthetic interactions and speaker programs
├── athena-results/
│   └── tables/compliance_risk_raw/mart_*/   ← dbt-materialized parquet tables (548 MB)
│                                            ← Glue Catalog Location URIs point here
│                                            ← DO NOT rename or move — breaks all Glue tables
└── athena-query-output/            ← Athena query result CSVs (transient)
```

> **Warning:** The `athena-results/tables/` prefix is referenced by Glue Catalog
> Location URIs for all four mart tables. Renaming or moving any path under it
> will break Glue table resolution for all downstream queries.

---

## Trustworthy AI evaluation framework

The project uses an 11-attribute Trustworthy AI framework as its evaluation rubric.

### The 11 attributes

| # | Attribute | Description |
|---|-----------|-------------|
| 1 | **Faithfulness** | Response claims are grounded in retrieved context (no hallucination) |
| 2 | **Retrieval Relevance** | The right chunks are fetched from Qdrant for a given query |
| 3 | **Groundedness of Decisions** | Anomaly flags and risk scores trace to observable data features (SHAP) |
| 4 | **Graceful Failure** | System degrades predictably when inputs are out-of-scope or retrieval fails |
| 5 | **Auditability** | Every agent action, score, and decision is logged to MLflow with a reproducible trail |
| 6 | **Consistency** | The same query produces stable outputs across runs (low variance) |
| 7 | **Latency** | Response times are within acceptable bounds for a compliance analyst workflow |
| 8 | **Robustness** | System handles noisy, incomplete, or adversarial inputs without catastrophic failure |
| 9 | **Calibrated Confidence** | Model's stated confidence reflects actual error rates |
| 10 | **Scope Adherence** | Agents stay within their designated domain and don't bleed into each other's responsibilities |
| 11 | **Reproducibility** | Given the same data and model artifacts, the pipeline produces the same outputs |

### Measurement groups

**Group A — Requires a golden dataset (RAGAS-style)**
- Faithfulness (attr #1)
- Retrieval Relevance (attr #2)
- Calibrated Confidence for Policy Agent answers (attr #9, partial)

**Group B — Direct measurements (no golden dataset needed)**
- Auditability (attr #5)
- Consistency (attr #6)
- Latency (attr #7)
- Scope Adherence (attr #10)
- Reproducibility (attr #11)

**Group C — Adversarial and SHAP analysis**
- Groundedness of Decisions (attr #3)
- Graceful Failure (attr #4)
- Robustness (attr #8)

### Execution order

**Group B → Group A → Group C**, on the following rationale:

- Group B yields 5 attributes of concrete numeric results without a golden dataset
  (mostly measurement of the running system).
- Group A requires a correctly-scoped golden dataset grounded in actual policy
  document contents. Do not reuse any LLM-generated golden dataset that was
  produced without policy document grounding — fabricated thresholds ($5,000 cap
  vs actual $75,000; $150 meals vs actual $25/$50/$100) invalidate all metrics.
- Group C requires Groups A and B as a baseline — adversarial testing means testing
  robustness of a known working system.
