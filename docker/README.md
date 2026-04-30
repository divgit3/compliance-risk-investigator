# Docker — Compliance Risk Investigator

Runs the full platform stack in containers:

| Service | Image | Port |
|---------|-------|------|
| `api` | `python:3.12-slim` (built from `docker/Dockerfile`) | 8000 |
| `mlflow` | `python:3.12-slim` | 5001 |
| `qdrant` | `qdrant/qdrant:latest` | 6333 |

Parquets and model outputs are mounted as **read-only volumes** — they are never
baked into the image. The image contains only code and Python dependencies.

---

## Prerequisites

### 1. Docker Desktop

Install from [docs.docker.com/get-docker](https://docs.docker.com/get-docker/).
Allocate at least **4 GB RAM** in Docker Desktop settings (SHAP loading needs ~2 GB).

### 2. Generate data files (run once on host)

```bash
# Activate venv
source venv/bin/activate

# Feature engineering
python features/feature_store.py

# Anomaly model + SHAP values (~45s)
python models/isolation_forest.py

# Full risk scores
python models/scorer.py

# Industry benchmarks (Athena fallback — no AWS needed)
python features/industry_benchmarks.py

# Rule-based flags
python models/rule_based_flags.py

# Policy document embeddings → populates qdrant_storage/
python pipelines/embed_policy_docs.py
```

After these steps the required paths must exist:
```
features/outputs/
  ├── feature_store.parquet
  ├── event_feature_matrix.parquet
  ├── hcp_spend_raw_dollars.parquet
  ├── competitor_benchmarks.parquet
  └── population_benchmarks.parquet
models/outputs/
  ├── risk_scores.parquet
  ├── rule_flags.parquet
  ├── if_scores.parquet
  ├── shap_values.parquet
  └── feature_importance.csv
compliance/
  └── rules.json
qdrant_storage/          ← populated by embed_policy_docs.py
mlflow.db                ← created by mlflow server or scorer.py
```

---

## Running the stack

```bash
cd docker

# 1. Copy and edit the environment file
cp .env.example .env
# Open .env and replace sk-... with your real OpenAI API key

# 2. Build and start all services
docker-compose up --build

# To run in the background:
docker-compose up --build -d
```

First build downloads ~1.5 GB of layers and installs Python dependencies (~3 min).
Subsequent builds use the layer cache and are much faster.

---

## Verifying the stack

```bash
# FastAPI health check
curl http://localhost:8000/health
# Expected: {"status":"ok"}

# List HCPs (top 5 by risk score)
curl "http://localhost:8000/hcps?limit=5"

# MLflow UI
open http://localhost:5001

# Qdrant collections
curl http://localhost:6333/collections
# Expected: {"result":{"collections":[{"name":"policy_docs"}]},...}

# Qdrant health
curl http://localhost:6333/readyz
```

---

## Running tests against the Docker stack

```bash
# In a separate terminal while docker-compose is running:
source venv/bin/activate

# CI-safe (no OpenAI calls)
pytest tests/test_api.py -m "not agent" -v

# Full suite including agent endpoints (real OPENAI_API_KEY required)
OPENAI_API_KEY=sk-... pytest tests/test_api.py -v
```

The test suite uses `httpx.ASGITransport` pointing at the in-process ASGI app
by default — it does NOT require a running Docker stack. To test against the
live Docker stack use a live HTTP client pointed at `http://localhost:8000`.

---

## Stopping the stack

```bash
cd docker

# Stop and remove containers (keep volumes)
docker-compose down

# Stop and remove containers + named volumes (mlflow_artifacts)
docker-compose down -v
```

---

## Individual service commands

```bash
# Rebuild only the api image
docker-compose build api

# Tail api logs
docker-compose logs -f api

# Open a shell inside the running api container
docker-compose exec api bash

# Restart a single service
docker-compose restart api
```

---

## Rebuilding after agent code changes

When you change any file under `agents/`, `api/`, or `streamlit_app/` the running
container does **not** pick up the change automatically (code is COPYed at build
time, not mounted). Rebuild explicitly:

```bash
# From the repo root
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml build --no-cache api
docker compose -f docker/docker-compose.yml up -d

# Verify the new image is running
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

`--no-cache` is required because Docker caches the `COPY . .` layer by timestamp.
On external/network volumes the timestamp may not change even when file content does,
causing the cache to serve stale code silently.

---

## Known limitations

| Limitation | Impact |
|-----------|--------|
| Parquets must be generated locally first | `docker-compose up` fails if `features/outputs/` or `models/outputs/` are empty |
| Qdrant storage must exist | Policy Q&A returns errors if `qdrant_storage/` is missing |
| SHAP values must be generated | `get_top_anomalous_features` falls back to Pearson proxy |
| Full 100pt EPS requires Athena | `engagement_priority_score` capped at ~57pts in dev (SOW component unavailable) |
| MLflow first-run is slow | MLflow image installs packages on first `docker-compose up` (~2 min) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Host machine                                                │
│                                                              │
│  features/outputs/*.parquet  ──────────────┐                │
│  models/outputs/*.parquet    ──────────────┤  :ro mounts    │
│  compliance/rules.json       ──────────────┤                │
│  mlflow.db                   ──────────────┤                │
│  qdrant_storage/             ──────────────┤                │
│                                            ▼                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  api :8000  │  │ mlflow:5001 │  │qdrant :6333 │        │
│  │  FastAPI    │  │  sqlite     │  │  policy_docs│        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└──────────────────────────────────────────────────────────────┘
```
