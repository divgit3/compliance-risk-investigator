# Local Setup & Debugging Notes

Working notes for spinning up `compliance-risk-investigator` locally after time away.
Complements the main README — this is operational knowledge, not the polished version.

**Last updated:** April 20, 2026

---

## Environment: Where things live

- **Project root:** `/Volumes/Career/Projects/compliance-risk-investigator/` (external drive — see caveats below)
- **Python venv:** `./venv/` (currently Python 3.9 — note: project memory lists 3.12, venv is actually 3.9)
- **Docker compose (canonical):** `./docker/docker-compose.yml`
- **Docker compose (deprecated): `./docker-compose.yml.deprecated` — renamed April 20, 2026, safe to fully delete in next cleanup pass
- **`.env` file for secrets** (AWS, OpenAI): `./docker/.env`
- **DuckDB:** `./pipelines/dbt_project/compliance_risk.duckdb` (symlinked from `./data/processed/compliance.duckdb`)

---

## Startup order from scratch (after time away or on fresh machine)

### 1. Activate venv
```bash
cd /Volumes/Career/Projects/compliance-risk-investigator
source venv/bin/activate
```

### 2. Load environment variables for local (non-Docker) scripts
`dbt` and other local scripts need `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OPENAI_API_KEY`, `ATHENA_S3_BUCKET` in the shell. The `.env` file lives under `docker/` (for Docker Compose), but local runs need them exported:

```bash
export $(grep -v '^#' docker/.env | xargs)

# Verify
echo $AWS_ACCESS_KEY_ID | head -c 8
aws sts get-caller-identity  # check creds are valid, not expired
```

**If creds are expired:** regenerate from wherever they were issued (AWS SSO, IAM user, etc.).

### 3. Rebuild DuckDB via dbt
The `compliance.duckdb` symlink points to `pipelines/dbt_project/compliance_risk.duckdb`. If the target file is missing or empty, run dbt:

```bash
cd pipelines/dbt_project
dbt run
cd ../..
```

**Expected result:** 10-11 models pass. 4 CMS-related models may fail with "Catalog awsdatacatalog does not exist" — this is the known Athena integration issue, not blocking for core pipeline.

**Verify DuckDB has required tables:**
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

Should show `mart_hcp_risk_profile: 97011 rows` at minimum, plus `mart_hcp_interactions_features`, `mart_violation_ground_truth`, etc.

### 4. Run feature pipelines (order matters)
```bash
python features/feature_store.py
python models/isolation_forest.py
python features/industry_benchmarks.py
# python pipelines/embed_policy_docs.py  # skip if Qdrant already populated
```

**Expected timings:**
- `feature_store.py`: ~10 seconds
- `isolation_forest.py`: ~50 seconds (SHAP computation is the bulk — ~45s)
- `industry_benchmarks.py`: ~3 seconds (with local fallback, no Athena)

### 5. Start Docker stack
```bash
cd docker
docker compose up -d
cd ..
```

If containers were already running and you just refreshed data files:
```bash
cd docker
docker compose restart api streamlit
cd ..
```

### 6. Verify services
- **Streamlit dashboard:** http://localhost:8502
- **FastAPI:** http://localhost:8000/docs
- **MLflow:** http://localhost:5002
- **Qdrant dashboard:** http://localhost:6335/dashboard

### 7. Smoke test
```bash
# API health
curl -s http://localhost:8000/health

# API returns rep data (critical for Rep-HCP Network page)
curl -s "http://localhost:8000/hcps?tier=critical&limit=1" | python -m json.tool | grep rep_id
# Should return: "primary_rep_id": "REP_XXXX"
```

---

## CRITICAL GOTCHA: Docker image caching

**After any code change to API or Streamlit, rebuild the image:**

```bash
cd docker
docker compose build api          # or 'streamlit' for Streamlit changes
docker compose up -d --force-recreate api
cd ..
```

**`docker compose restart` and `--force-recreate` alone do NOT pick up code changes.** They reuse the existing image. Only `docker compose build` rebuilds with current code.

**How to tell if you're hitting this issue:** check what code is in the running container:
```bash
docker exec docker-api-1 grep -A 4 'keyword_from_your_change' /app/api/routers/relevant_file.py
```

If the container shows old code but your local file has new code → needs rebuild.

---

## Known issues / TODO

### Resolved April 20, 2026 ✅
- [x] **`config.py` port default** → fixed from 8001 to 8000
- [x] **DuckDB broken symlink** → `dbt run` regenerates `compliance_risk.duckdb`
- [x] **API `/hcps` endpoint missing `primary_rep_id`** → was stale Docker image; fixed by `docker compose build api && up -d --force-recreate api`
- [x] **`streamlit_agraph` missing from Docker image** → added to `streamlit_app/requirements.txt`, rebuilt container
- [x] **Rep-HCP Network page showed 0 reps/edges** → multiple causes: (a) pipelines not run, (b) stale Docker image. Both resolved.
- [x] **`awswrangler` missing from requirements.txt** → added `awswrangler>=3.14.0`
- [x] **Stale root-level `docker-compose.yml`** → renamed to `docker-compose.yml.deprecated`, safe to fully delete in next cleanup pass

### Open — high priority
- [ ] **Rep-HCP Network page shows 0 reps/edges** if data pipelines haven't been re-run after time away. Fix: run `feature_store.py` → `isolation_forest.py` → `industry_benchmarks.py` in order.

### Open — medium priority (cleanup / hygiene)
- [ ] **Python version mismatch.** Venv uses Python 3.9, project memory/docs reference 3.12. Recreate venv with 3.12 before end-of-project cleanup.
- [ ] **`.env` file discoverability.** Lives under `docker/` for Docker Compose, but local scripts need it in shell env. Options: (a) symlink `docker/.env` to repo root, (b) script wrapper that exports before running, (c) document the export pattern (this doc).
- [ ] **Fully delete `docker-compose.yml.deprecated`** in a future cleanup pass once confident nothing references it.

### Open — low priority (infrastructure work — Phase 5)
- [ ] **Athena integration — bucket naming.** Default `s3://compliance-athena-results/` is taken globally by another AWS user. Fix: create unique bucket in `us-east-2` (e.g., `compliance-athena-results-divya`), update `ATHENA_S3_BUCKET` env var in `docker/.env`.
- [ ] **Athena integration — region mismatch.** Existing `compliance-risk-investigator` bucket is in `us-east-1` (`LocationConstraint: null`), but Athena workgroup is in `us-east-2`. Athena requires same-region S3 for query results.
- [ ] **Athena/Glue federation.** 4 dbt models (`stg_cms_general_payments` and downstream) fail because DuckDB isn't federated to AWS Glue. Options: install `duckdb-athena` extension, refactor models to read CMS data from S3 directly, or drop CMS-dependent models. This is the "Athena schema fix (permanent)" item from Phase 5 backlog.
- [ ] **External drive considerations.** Project lives on `/Volumes/Career/` external drive. Symlinks, Docker volume mounts, and absolute paths can behave unpredictably if drive is remounted with a different name. Consider moving to `~/Projects/` or similar at some point.
- [ ] **Docker dev workflow lacks auto-rebuild on code change.** Currently requires manual `docker compose build` after code edits. Add docker-compose watch configuration for hot reload in dev mode.

### Phase 5 feature backlog (separate from infrastructure)
- Recency-weighted risk score
- SHAP feature importance chart (UI)
- Interaction-based rep network (multi-rep per HCP)
- Flags API enrichment (actual vs threshold values)
- Model comparison (IF vs LOF vs OCSVM)
- Temporal train/test splits
- Policy citation quality improvements
- LangGraph supervisor agent

---

## Fresh model insights (April 20, 2026)

After rerunning isolation forest on current data:

**Scoring summary:**
- Total HCPs scored: 97,011
- Features used: 99
- Outliers flagged: 9,701 (10.0%)
- Anomaly score median: 3.51
- Anomaly score p95: 30.12
- Total compute time: 52.8s (SHAP computation: 44.3s)

**Top 5 features by mean absolute SHAP value:**
1. `fmv_compliance_rate` (0.172)
2. `interactions_with_vague_rationale` (0.161)
3. `multi_year_increasing_flag` (0.146)
4. `avg_meal_cost` (0.131)
5. `interaction_frequency_score` (0.125)

**Why this matters for interviews:** know these cold. When asked "what features drive risk in your model?" — answer with this list and be able to explain why each one makes intuitive sense in a pharma compliance context.

---

## Session debugging log — April 20, 2026 (first day back after ~1 week off)

What went wrong and what was learned:

1. **Streamlit couldn't reach FastAPI (port error).** `config.py` had default port 8001; API was on 8000. Also, Dockerized Streamlit was already running at :8502 so local `streamlit run` was redundant. Fix: use the Dockerized version, change config default.

2. **`streamlit_agraph` ModuleNotFoundError on Rep-HCP Network page.** Missing from Docker image. Added to `streamlit_app/requirements.txt`, rebuilt.

3. **Rep-HCP Network page showed 0 reps / 0 edges** — initially diagnosed as "data pipelines not run" (partially true), but actual root cause was stale Docker image missing `primary_rep_id` in the `/hcps` endpoint code. See #9 below.

4. **`python3` vs `python` confusion.** `python3` sometimes picked up system Python 3.9 instead of venv Python, causing missing module errors. **Always use `python` (not `python3`) with venv active.**

5. **`feature_store.py` failed: DuckDB doesn't exist.** File at `data/processed/compliance.duckdb` is a broken symlink to `pipelines/dbt_project/compliance_risk.duckdb` which had been deleted. **Fix:** `cd pipelines/dbt_project && dbt run`.

6. **`dbt run` failed: AWS_ACCESS_KEY_ID not set.** `.env` is under `docker/`, not loaded into local shell by default. **Fix:** `export $(grep -v '^#' docker/.env | xargs)`.

7. **`dbt run` partial success: 4 models failed with "awsdatacatalog does not exist".** Athena federation not set up in DuckDB. Not blocking for core pipeline. Noted in Phase 5 backlog.

8. **`industry_benchmarks.py` Athena connection issues (cascading):**
   - First: `awswrangler` not installed → `pip install awswrangler`
   - Second: bucket `compliance-athena-results` doesn't exist AND name is taken globally
   - Third: existing `compliance-risk-investigator` bucket is in us-east-1, Athena in us-east-2
   - Resolution: accepted local fallback (SOW=NaN, EPS capped ~45-70pts), deferred permanent fix to Phase 5

9. **API `/hcps` endpoint missing `primary_rep_id` in response.** Ultimate root cause: **Docker image was stale.** Local code had `primary_rep_id` in the `cols` list (line 57 of `api/routers/hcps.py`), but the Docker image was built before that change. `docker compose restart` and `--force-recreate` alone don't pick up code changes — they reuse the existing image. **Fix:** `docker compose build api && docker compose up -d --force-recreate api`.

10. **Diagnostic technique learned:** When API responses don't match code expectations, check what's actually in the running container:
    ```bash
    docker exec docker-api-1 grep -A 4 'pattern' /app/api/routers/file.py
    ```
    If container code differs from local code → image needs rebuild.

11. **Isolation Forest perf baseline:** 97K rows × 99 features takes ~53s end-to-end, SHAP computation dominates (44s of that). Good reference for future performance comparisons.

---

## Where "Phase 4 truly complete" sits

Repo commit `f51372b` ("Phase 4 — Streamlit compliance dashboard complete") suggests Phase 4 is done.
Truly complete means all of the following are on `main`:

- [x] Streamlit dashboard with 5 pages (shipped)
- [x] Rep-HCP Network with state filter and leaderboard (shipped, commit `cf8253b`)
- [x] API exposes `primary_rep_id` for Network graph (fixed April 20)
- [x] `streamlit-agraph` in Docker image (fixed April 20)
- [x] `config.py` port default correct (fixed April 20)
- [ ] SHAP visualization on HCP Detail page
- [ ] RAGAS evaluation suite with golden dataset + threshold gates
- [ ] README framework alignment (NIST AI RMF, SR 11-7 mapping)
- [ ] Setup docs updated (this file + main README section for local setup)
- [ ] Two docker-compose files reconciled
