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
---

## Daily Startup — Quick Reference

### Scenario 1: Everything worked recently, just need to restart

```bash
cd /Volumes/Career/Projects/compliance-risk-investigator
source venv/bin/activate

# Start Docker stack (all 4 services)
cd docker && docker compose up -d && cd ..

# Wait ~30 seconds, then smoke-test
sleep 30
curl -s http://localhost:8000/health
curl -s "http://localhost:8000/hcps?tier=critical&limit=1" | python -m json.tool | grep rep_id

# Open dashboard
open http://localhost:8502
```

If smoke tests pass → ready to work. If not → go to Scenario 2.

### Scenario 2: After time away (1+ week) or data looks stale

```bash
cd /Volumes/Career/Projects/compliance-risk-investigator
source venv/bin/activate

# 1. Load env vars for local scripts
export $(grep -v '^#' docker/.env | xargs)
aws sts get-caller-identity   # verify AWS creds valid

# 2. Regenerate DuckDB if empty
cd pipelines/dbt_project
dbt run
cd ../..

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

# 6. Open dashboard
open http://localhost:8502
```

### Scenario 3: After code changes to API or Streamlit

Code changes require Docker image rebuild (see "CRITICAL GOTCHA" section below):

```bash
cd docker
docker compose build api          # or 'streamlit'
docker compose up -d --force-recreate api
cd ..
```

### Shutdown (end of work session)

```bash
cd docker
docker compose down
cd ..
```

Stops all containers cleanly. Data persists in volumes. Next `docker compose up -d` resumes.

---

## Checkpoints — "Is the app actually working?"

Quick visual verification after startup:

- [ ] `curl http://localhost:8000/health` returns `{"status":"ok"}`
- [ ] `curl "http://localhost:8000/hcps?tier=critical&limit=1"` returns `primary_rep_id` in response
- [ ] http://localhost:8502 loads without errors
- [ ] **Compliance Risk Overview** page shows ~291 critical HCPs
- [ ] **Rep-HCP Network** page shows red squares (reps), lines (edges), and leaderboard populated
- [ ] **HCP Explorer** page lists HCPs sortable by risk score
- [ ] **HCP Detail** page (pick any HCP) shows risk score, flags, features
- [ ] **Policy QA** page returns answers with citations for a simple question

If any of these fail, check which Scenario above applies.

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
- [x] **Athena integration — bucket naming.** Default `s3://compliance-athena-results/` is taken globally by another AWS user. COMPLETED April 21, 2026 (commits `160feb9` + `fa2e924`). Real bucket is `s3://compliance-risk-investigator/` in `us-east-1` (NOT `us-east-2` — that was a CLI region drift issue, not an actual region mismatch). Code now uses `s3://compliance-risk-investigator/athena-query-output/` for query result CSVs, kept separate from `athena-results/tables/` which holds dbt-materialized table parquet (548 MB, load-bearing for Glue Catalog Locations).
- [x] **Athena integration — region mismatch.** Existing `compliance-risk-investigator` bucket is in `us-east-1` (`LocationConstraint: null`), but Athena workgroup is in `us-east-2`. COMPLETED April 21, 2026 (commit `160feb9`). The original framing was incorrect — investigation confirmed the Athena workgroup ARN is `arn:aws:athena:us-east-1:858000384282:workgroup/primary` (us-east-1, same as bucket). The actual issue was local AWS CLI region drift (`aws configure get region` returned `us-east-2`), fixed permanently with `aws configure set region us-east-1`.
- [ ] **Athena/Glue federation.** 4 dbt models (`stg_cms_general_payments` and downstream) fail because DuckDB isn't federated to AWS Glue. Options: install `duckdb-athena` extension, refactor models to read CMS data from S3 directly, or drop CMS-dependent models. This is the "Athena schema fix (permanent)" item from Phase 5 backlog.
- [ ] **External drive considerations.** Project lives on `/Volumes/Career/` external drive. Symlinks, Docker volume mounts, and absolute paths can behave unpredictably if drive is remounted with a different name. Consider moving to `~/Projects/` or similar at some point.
- [ ] **Docker dev workflow lacks auto-rebuild on code change.** Currently requires manual `docker compose build` after code edits. Add docker-compose watch configuration for hot reload in dev mode.

---

## Credential Rotation Procedure

When AWS or OpenAI keys need rotating (compromise, periodic rotation, employee turnover), follow this checklist. The two-file structure for AWS is intentional — Docker containers and the host CLI run in separate environments and read keys from different files.

### AWS Key Rotation

AWS access keys must be updated in **TWO files**:

- `~/.aws/credentials` — used by host CLI (`aws ...` commands), host Python scripts, and dbt commands
- `docker/.env` — used by Docker containers (API agents, Streamlit, anything running inside docker compose)

**Procedure:**

1. AWS Console → IAM → Users → divyaiam → Security credentials → "Create access key"
2. Note the new Access Key ID and Secret Access Key in a secure location (1Password, etc.)
3. Open `~/.aws/credentials` in a text editor (e.g., `nano ~/.aws/credentials` or `cursor ~/.aws/credentials`). Update `aws_access_key_id` and `aws_secret_access_key` lines.
4. Open `docker/.env` in a text editor. Update `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` lines.
5. Verify both files have matching keys using last-4-char comparison (safe — does not expose keys):
   ```bash
   grep "AKIA" ~/.aws/credentials | grep -oE "[A-Z0-9]{4}$"
   grep "AWS_ACCESS_KEY_ID" docker/.env | grep -oE "[A-Z0-9]{4}$"
   ```
   Both commands should print the same 4-char suffix
6. Verify CLI auth works with new key: `aws sts get-caller-identity`
7. Restart Docker containers to load the new env: `cd docker && docker compose down && docker compose up -d && cd ..`
8. Verify container picks up new key (safe output — only suffix): `docker exec docker-api-1 sh -c 'echo "$AWS_ACCESS_KEY_ID" | grep -oE "[A-Z0-9]{4}$"'`
9. Wipe dbt build artifacts that may contain the old key (they regenerate on next dbt run): `rm -rf pipelines/dbt_project/target/ pipelines/dbt_project/logs/`
10. AWS Console → IAM → deactivate the old key (don't delete for 24hr in case rollback needed)
11. After 24hr with no issues, delete the old key entirely

### OpenAI API Key Rotation

OpenAI key only lives in **ONE place**: `docker/.env` — used by Docker containers via `os.environ.get("OPENAI_API_KEY")` patterns in agent code.

**Procedure:**

1. platform.openai.com/api-keys → Revoke the old key
2. Create a new key, save it securely
3. Open `docker/.env` in a text editor. Update the `OPENAI_API_KEY` line.
4. Restart containers: `cd docker && docker compose down && docker compose up -d && cd ..`
5. Verify (counts the var, doesn't print value): `docker exec docker-api-1 env | grep -c "^OPENAI_API_KEY"` — should print `1`

### Critical Safety Rules

- **Never** paste full `.env` or `~/.aws/credentials` contents into chat tools, logs, screenshots, or commits
- **Never** commit `.env` files (already gitignored, but worth re-confirming with `git status` after edits)
- **Never** export keys at the shell prompt — the command enters `~/.zsh_history` and persists across sessions
- **Use the inline-env pattern** when you need an env var for a single command. Example: `OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" docker/.env | cut -d= -f2-) python3.12 -m pytest tests/`. This sets the env var only for that one command and never enters shell history.
- **Use the redacted display pattern** when you need to inspect env files: `cat docker/.env | sed -E 's/(KEY|SECRET|TOKEN|PASSWORD)=.+/\1=***REDACTED***/g'`
- dbt's `on-run-start` hook in `dbt_project.yml` resolves env vars and writes them to `target/` artifacts. Always wipe `target/` after key rotation.
- Key suffix comparison (`grep -oE "[A-Z0-9]{4}$"`) is the safe way to verify two files have matching keys without exposing the key itself.

---

### Phase 5 feature backlog (separate from infrastructure)

#### Open Phase 5 Items
- Recency-weighted risk score
- SHAP feature importance chart (UI)
- Interaction-based rep network (multi-rep per HCP)
- Flags API enrichment (actual vs threshold values)
- Model comparison (IF vs LOF vs OCSVM)
- Temporal train/test splits
- Policy citation quality improvements
- LangGraph supervisor agent
- **Feature correlation cleanup in SHAP display.** Observed April 20: correlated features crowd the top-N SHAP list (e.g., `spend_2024` and `annual_cap_pct_used_2024` both appear in top 10 but convey similar signal — spend dollars vs. percentage of compliance cap consumed). Options: (a) hierarchical feature grouping, (b) de-duplication via correlation threshold, (c) SHAP interaction values. Good interview story: correlated features make single-feature SHAP misleading; production fix would involve feature engineering before explanation.
- [ ] **Investigate synthetic generator: spend-interaction correlation** (estimated 1-2 hours)
  - Pre-read (~30-45 min before any code work):
    - `docs/implementation/phase1_implementation.md` (full read) — explains synthetic data structure
    - `docs/implementation/phase2_implementation.md` lines 79-80 — already-documented limitations
    - `pipelines/ingest/synthetic_generator.py` functions: `generate_hcp_master()` and related interaction/payment generation logic
  - Investigation questions (smaller scope now that we know some characteristics are documented):
    - Why is the spend-interaction correlation so low (0.068)? Is this an intentional design choice tied to the violation generation model, or an unintended side effect of independent random distributions?
    - What's the rationale documented for "CMS spend values not scaled to realistic cap thresholds" (Phase 2 doc line 79)?
    - Would a more realistic synthetic data design (with correlated spend + interactions like real CMS data) materially change risk model behavior, or would the composite score still surface the same risk patterns?
  - Decision after investigation:
    - If documented design choices: add a clear "synthetic data limitations" note to README and Medium article, no code changes needed.
    - If unintended: scope a synthetic data v2 redesign as a separate Phase 5 item (NOT a today fix — would cascade through every downstream artifact).
- [ ] **Fix: synthetic speaker generator bypasses CMS reconciliation invariant for priority speakers** (decision needed tomorrow, investigation completed April 21 2026)

   **The design intent (confirmed):**

   CMS Open Payments data provides the per-HCP-per-year total dollar amount as ground truth (regulatory source of truth). The synthetic generator's role is to split that CMS total across multiple internal event types (interactions, speaker events, meals, consulting) — so the SUM of synthetic event dollars should equal the CMS total for each HCP per year (reconciliation invariant). Internal events don't invent new money; they show WHERE the CMS dollars went.

   **The bug:**

   `generate_speaker_events()` at `pipelines/ingest/synthetic_generator.py` line 689 creates a `is_priority_speaker = True` branch for HCPs with `fmv_tier in ('regional', 'national')` OR `is_kol=True`. For these HCPs, the speaker fee generation bypasses the CMS-share cap (lines 710-712):

   ```python
   if cms_total_yr >= 500:
       max_fee_per_event = max(50.0, cms_total_yr * 0.40 / n_events)
   else:
       max_fee_per_event = float("inf")  # FMV rate card is only ceiling
   ```

   So priority speakers with low CMS totals generate speaker events at full FMV rate card values ($2,000-$10,000 per event) that can't plausibly reconcile with the HCP's real CMS total.

   **Scale of the problem (investigated April 21, 2026):**

   - Total speaker events in dataset: 5,241
   - Events where `speaker_fee > 40%` of CMS total (violates reconciliation): 5,182 (98.9%)
   - Events for HCPs with $0 CMS total in that year: 2,418 (46%)
   - Events where CMS total < $500: 5,179 (98.8%)
   - Events where CMS total $500–$3,000: 19 (0.4%)
   - Events where CMS total >= $3,000: 43 (0.8%)

   This is not a minor edge case — the priority speaker branch is where the vast majority of speaker events come from.

   **Key real-world context:**

   In real pharma compliance, a $15K speaker fee MUST be reported to CMS by federal law. An HCP with $17.60 in CMS but $15K in internal speaker fees would be a serious under-reporting violation triggering OIG investigation. The synthetic data cannot contain this scenario without breaking its own design premise.

   **Three design options to decide tomorrow:**

   Option Y (pure) — Apply CMS-share cap to everyone, remove priority branch
   - Result: 62 speaker events remain (from 5,241)
   - Pros: Strictly respects reconciliation invariant; realistic
   - Cons: 62 events is too few for a meaningful compliance risk model across 97,011 HCPs; destroys the speaker-program compliance angle of the project
   - Honest assessment: Too destructive to use as-is

   Option Y (modified) — Expand CMS source filter to include more Takeda payments
   - Result: Depends on filter expansion; could restore reasonable event volume
   - How: Review `TARGET_FILTER = "takeda"` in `synthetic_generator.py` and the CMS filter logic (`load_cms_hcp_totals`). Currently may filter too narrowly. Widening criteria (include subsidiaries, drug name matches, etc.) could increase per-HCP CMS totals and make priority speaker scenarios realistic.
   - Pros: Preserves both realism AND event volume; aligns with how real pharma subsidiaries get aggregated in CMS analysis
   - Cons: Requires re-running expensive CMS ingestion pipeline; behavior change affects all downstream data

   Option Z (hybrid) — Keep the priority speaker branch but synthetically augment CMS totals to match
   - Result: 5,241 events preserved; CMS totals artificially inflated for priority speakers
   - Pros: Minimal data loss; preserves current dataset shape
   - Cons: Explicitly breaks the "CMS data is real Takeda data" property; must be clearly documented as synthetic augmentation everywhere
   - Honest assessment: Feels like papering over the real issue

   **What the decision depends on (to think about tomorrow):**

   - Is the project's compliance risk story stronger with "realistic synthetic data that's tiny" or "data-volume that's acknowledged-synthetic"? Depends on how the Medium article frames the work.
   - Would expanding CMS filter (Option Y modified) produce enough plausible priority-speaker HCPs?
   - What does the test suite assume about event counts? Some fixtures may break.

   **Key code locations:**
   - Priority speaker branching: `pipelines/ingest/synthetic_generator.py` line 689
   - Fee cap logic: lines 704-712
   - Speaker fee assignment: line 745
   - CMS source filter: line 50 (`TARGET_FILTER = "takeda"`)
   - `load_cms_hcp_totals()` function: line 193

   Not decided today; picking tomorrow with fresh judgment.

#### Resolved by Design
- **Streamlit container workflow trade-off (resolved by design — no action needed)**
  - Historical: Original architecture mounted `../streamlit_app:/app:ro` as a volume, giving hot-reload for code changes
  - Today's change (Option C): Removed streamlit_app volume mount entirely. Code now lives in the image via `COPY streamlit_app/ .` during Docker build. Only `features/outputs` is volume-mounted for data access.
  - Consequence: Streamlit code changes require `docker compose build streamlit && docker compose up -d --force-recreate streamlit` (~30 seconds) instead of save-and-refresh hot-reload
  - Status: Accepted trade-off. No restoration work needed — there is no :ro mount to restore on streamlit_app because that mount no longer exists. Architecture is cleaner (immutable-image pattern matching production deployment).

#### Completed (April 20, 2026)

**MLflow container fix — commits `da0fc61` + `540e70b`**

Two-phase fix for broken MLflow container (HTTP server accepted TCP connections but reset HTTP requests, caused by `python:3.12-slim` + `pip install mlflow==3.10.1` runtime pattern).

- **Phase 1 — morning workaround (`da0fc61`):** Made MLflow tracking optional via `MLFLOW_ENABLED` env var. Unblocked Investigation/Monitoring/Policy agents which were timing out at 120s due to broken MLflow HTTP server.
- **Phase 2 — evening proper fix (`540e70b`):** Replaced `image: python:3.12-slim` + runtime pip install with the official pre-built image `ghcr.io/mlflow/mlflow:v3.10.1`. Added `--allowed-hosts mlflow,mlflow:5001,localhost,localhost:5002,127.0.0.1,127.0.0.1:5002` flag to satisfy DNS rebinding protection middleware introduced in MLflow 3.5.0+ (GitHub issue #22095 — ports don't strip in matching, so hostname and hostname:port variants both needed). Re-enabled `MLFLOW_ENABLED=true` in API service.
- **Verified end-to-end:** policy_agent, monitoring_agent, investigation_agent experiments auto-created in MLflow on first agent call. All three agents respond correctly via browser UI (Policy returns `$75,000` cap with rule `COMP_001`, Investigation completes in ~3s, Monitoring normal). MLflow API returns 200 with experiment count = 4.
- **Unblocks:** Attribute #5 Auditability evaluation in Phase 5 Trustworthy AI plan.

**Python 3.9 → 3.12 venv upgrade — commit `1106a79`**

Upgraded local development venv from Python 3.9.6 to 3.12.13.

- First attempt earlier in day failed at `urllib3==1.26.20` dependency conflict (botocore/requests/docker wanted `<3,>=1.26.0` but types-requests wanted `>=2`). Rolled back cleanly.
- Second attempt succeeded by dropping the urllib3 pin from the install manifest and letting pip resolve — it settled on `urllib3-2.6.3` which satisfies all dependencies.
- 160 packages installed cleanly on Python 3.12. Verified critical imports: `pandas 2.3.3`, `streamlit 1.50.0`, `mlflow 3.1.4`, `httpx 0.28.1`.
- `.venv_py312_install.txt` kept as reproducibility artifact (gitignored, not committed).
- `.venv_py39_snapshot.txt` retained as historical reference.
- Old Python 3.9 `venv_py39_backup` directory removed after verification.

**Docker Compose cleanup — commit `c3eb75e`**

Removed obsolete top-level `version: "3.9"` attribute from `docker/docker-compose.yml`. Docker Compose v2+ infers schema from service structure; the `version` field was printing a WARN on every compose invocation. Compose config validates silently now.

#### Completed (April 21, 2026)

**Athena cleanup — commits `160feb9`, `fa2e924`, `21b34eb`**

Summary: Restored the Athena query path in `features/industry_benchmarks.py` that had been silently failing since Phase 3 was first written, plus broader cleanup of Athena-related env vars, S3 paths, and CLI configuration. Side effect: 88/88 tests now pass (was 79/88 with 8 OpenAI agent test failures gated by host env config).

**Key findings (for future debugging or Medium article)**

- The Athena code path had never worked correctly. Phase 3's `industry_benchmarks.py` was authored against an imagined table shape (pre-aggregated columns named `total_spend_2024`, `specialty`, `payment_year`) that did not match the actual dbt model output (raw payment rows with columns `program_year`, `physician_specialty`, `payment_amount`). Because the script wrapped Athena calls in a `try/except` block that fell back to local parquet on ANY exception, the failure was invisible — logs always showed "Athena not reachable — using local fallback" regardless of whether Athena was actually reachable.
- The "us-east-2 region mismatch" framing in old backlog items was wrong. The original notes said Athena was in `us-east-2` but the bucket was in `us-east-1`. Investigation today confirmed via AWS console that the Athena workgroup ARN is `arn:aws:athena:us-east-1:858000384282:workgroup/primary` — same region as the bucket. The actual issue: local AWS CLI was configured for `us-east-2` (`aws configure get region` returned `us-east-2`), so any host-side `aws glue ...` or `aws athena ...` commands looked in the wrong region and got "Entity Not Found." Fixed permanently with `aws configure set region us-east-1`.
- Glue Catalog is decoupled from CLI region. S3 commands (`aws s3 ls`) worked fine despite the CLI region drift because S3 is a global service. But Glue is region-scoped, so `aws glue get-databases` returned empty until region was fixed. This was the original signal that "something was wrong with Athena."
- Three layers of bugs in `industry_benchmarks.py`:
  1. **Broken bucket reference:** `_ATHENA_BUCKET` fallback pointed at `s3://compliance-athena-results/`, a globally-taken bucket name not owned by this AWS account (returns 403 Forbidden, not 404).
  2. **Wrong fallback database name:** `_ATHENA_DB` fallback was `compliance_db`, but actual Glue database is `compliance_risk_raw`.
  3. **SQL schema mismatches:** queries referenced `payment_year` (correct: `program_year`), `specialty` (correct: `physician_specialty`), and `total_spend_2024` (does not exist — must aggregate from raw `payment_amount`).
- Population query needed full rewrite, not just column rename. The original assumed pre-aggregated yearly totals as a column. Reality: `mart_population_payments` is `SELECT * FROM stg_cms_general_payments WHERE hcp_id IN (target_hcps)` — raw payment rows. Rewrite uses inner subquery to compute per-HCP yearly totals, then aggregates across HCPs by `physician_specialty`.
- Restoration revealed real industry data. Pre-fix outputs were Nova-only (fallback path):
  - EPS full mean: 12.40 → 28.61
  - HCPs with SOW: 0 → 73,409
  - Population avg spend: $195.06 → $4,386.33
  - `engagement_priority_score` was capped at ~70pts in fallback, now reaches up to 92pts (full 100pt range)

**S3 architecture (current state)**

`s3://compliance-risk-investigator/` — single bucket, `us-east-1`
- `raw/cms_open_payments/` — source CMS data, referenced by Glue table `cms_open_payments` (CSV)
- `synthetic/` — generated synthetic interactions, speaker programs, attendees
- `athena-results/tables/compliance_risk_raw/mart_*/\<UUID\>/` — dbt-materialized parquet tables (548 MB across 4 mart tables). Glue Catalog Location URIs point at these. **DO NOT rename or move** — would break all Glue tables.
- `athena-query-output/` — Athena query result CSVs (transient, configured as primary workgroup default location)

**Code architecture (current state)**

- AWS region: env var `AWS_DEFAULT_REGION` (Python boto3) and `AWS_REGION` (some other SDKs), default `us-east-1`
- Athena database: env var `ATHENA_DATABASE`, default `compliance_risk_raw`
- Athena S3 query output: env var `ATHENA_S3_BUCKET`, default `s3://compliance-risk-investigator/athena-query-output/`
- Athena S3 staging dir (for pyathena): env var `ATHENA_S3_STAGING_DIR`, same default
- Two file locations for AWS keys: `docker/.env` (containers) + `~/.aws/credentials` (host CLI). See "Credential Rotation Procedure" section.

**Test infrastructure changes**

- `tests/test_api.py::test_benchmarks_peer_count_is_97011` renamed to `test_benchmarks_peer_count_is_specialty_scoped`. Old test asserted full population peer count (97,011); new test asserts specialty-scoped subset (correct post-Athena-fix behavior).
- Added `pytest>=8.0.0,<10.0.0` and `pytest-asyncio>=0.23.0,<2.0.0` to `docker/requirements-api.txt` so tests can run inside the API container with the OpenAI key already in env (no host shell key juggling).

**Side note on stale artifacts**

Any model artifacts (pickled models, MLflow runs) trained against the OLD `competitor_benchmarks.parquet` and `population_benchmarks.parquet` files (pre-fix degraded values) are now technically stale. See backlog item "Stale artifact audit (post-Athena fix)" for follow-up.

#### Investigation findings (April 21, 2026)

##### Finding: Risk score is decoupled from spend volume — HCP_357811 case study

Investigation question: Why does HCP_357811 (test fixture's KNOWN_CRITICAL_ID, the top critical-risk HCP) show $0 spend in 2024?

Answer: Risk score is correctly decoupled from payment volume. HCP_357811's profile:

- Total spend: only $17.60 (single payment in 2023, $0 in 2022/2024)
- Total interactions: 7 over 3 years
- Risk score: 94.12 (top critical tier)

The high risk score is driven by persistent documentation and process violations, NOT by dollar volume:
- 71% of events missing attestations (`pct_events_missing_attestation`: 0.71)
- 5 missing-attestation flags fired
- 5 repeat-speaker flags (suspicious pattern of same speaker appearing repeatedly)
- 2 rapid-repeat flags (events scheduled too close together)
- 2 high-venue-cost flags
- Cost-per-head exceeded limits 7 times across 4 meals (avg $64/meal)
- 3 interactions with vague compliance rationales
- `attendees_signed_pct_min` = -3.0 (severe under-attendance vs claimed — potential ghost speaker programs)
- 13 ground truth violations, max severity = "high"

This is a meaningful finding for two reasons:

1. **Model validation:** The composite risk score correctly identifies process-violation patterns over spend-volume patterns. A naive "biggest spenders are biggest risks" model would miss HCP_357811 entirely. The model surfaces them because it weights documentation gaps and attendance anomalies, not just dollar amounts.

2. **Interview/article narrative:** This directly contradicts the obvious assumption that high-risk HCPs are the high-spend HCPs. In real pharma compliance work, the riskiest HCPs are often process violators — speakers with ghost programs, attestation gaps, vague rationales — not the top-paid consultants. The dataset reflects this.

Implication for downstream work:
- When writing the Medium article, this is a paragraph or sidebar worth including
- When validating model behavior in Phase 5, this finding gives a concrete test case for "risk score should not correlate strongly with `spend_2024` alone"

##### Caveat (added after follow-up analysis)

Follow-up exploration revealed the synthetic dataset has properties that affect how this finding should be interpreted:

- Correlation between `total_interactions` and `spend_2024` across the population: 0.068 (essentially uncorrelated)
- 79.6% of HCPs (77,242 of 97,011) have under $100 in spend across all 3 years combined
- HCPs with 5-10 interactions have median spend of -0.35 (z-scored — feature_store stores standardized values, not raw dollars)

Phase 2 implementation doc explicitly documents this characteristic at lines 79-80:

> "Synthetic data bias: Annual cap breach flags fire for ~37% of HCPs in synthetic data (CMS spend values not scaled to realistic cap thresholds), creating inflated critical/high flag counts vs production."
> "GT recall ceiling: 41% high+critical recall reflects the synthetic violation label generator's limited correlation with rule flags — production recall is expected to be higher with real violation ground truth."

This means the "decoupling of risk score from spend volume" pattern reflects synthetic data generator design, not necessarily a discovered insight about real-world pharma compliance behavior. Real CMS Open Payments data typically shows substantial correlation between interaction frequency and spend volume.

Honest framing for any future article or paper:

- **TRUE:** This synthetic dataset contains an HCP profile (HCP_357811) where high-risk score correlates with documentation/process violations rather than payment volume.
- **TRUE:** The composite risk score model correctly weights process-violation features given the input data.
- **NOT YET ESTABLISHED:** Whether the model would identify the same "process violator over high spender" pattern in real CMS data, because the synthetic data does not preserve realistic spend-violation correlations.

See backlog item below for the planned investigation to understand the synthetic generator's design decisions.

---

## Phase 4/5 — Trustworthy AI Evaluation Plan

The Compliance Risk Investigator uses an 11-attribute Trustworthy AI framework as its evaluation rubric. These attributes split into three groups based on what's required to measure them:

### The 11 Attributes

1. **Faithfulness** — response claims are grounded in retrieved context (no hallucination)
2. **Retrieval Relevance** — the right chunks are being fetched from Qdrant for a given query
3. **Groundedness of Decisions** — anomaly flags and risk scores trace back to observable data features (SHAP)
4. **Graceful Failure** — the system degrades predictably when inputs are out-of-scope or retrieval fails
5. **Auditability** — every agent action, score, and decision is logged to MLflow with a reproducible trail
6. **Consistency** — the same query produces stable outputs across runs (low variance)
7. **Latency** — response times are within acceptable bounds for a compliance analyst workflow
8. **Robustness** — the system handles noisy, incomplete, or adversarial inputs without catastrophic failure
9. **Calibrated Confidence** — the model's stated confidence (risk tiers, anomaly scores) reflects actual error rates
10. **Scope Adherence** — agents stay within their designated domain (Investigation, Monitoring, Policy) and don't bleed into each other's responsibilities
11. **Reproducibility** — given the same data and model artifacts, the pipeline produces the same outputs

### Group A — Needs Golden Dataset (RAGAS-style)
- Faithfulness (attr #1)
- Retrieval Relevance (attr #2)
- Calibrated Confidence for Policy Agent answers (attr #9, partial)

### Group B — Direct Measurements (no golden dataset needed)
- Auditability (attr #5)
- Consistency (attr #6)
- Latency (attr #7)
- Scope Adherence (attr #10)
- Reproducibility (attr #11)

### Group C — Adversarial & SHAP Analysis
- Groundedness of Decisions (attr #3)
- Graceful Failure (attr #4)
- Robustness (attr #8)

### Execution Order

Group B → Group A → Group C, on the following rationale:
- Group B yields 5 attributes' worth of concrete numeric results with relatively fast implementation (no golden dataset required, mostly measurement-of-running-system).
- Group A requires a correctly-scoped golden dataset. A prior golden dataset attempt (generated outside the repo by an LLM without grounding in actual policy docs) contained fabricated Nova Pharma rules — $5,000 cap vs actual $75,000, $150 meals vs actual $25/$50/$100. Do not reuse. Rebuild from actual policy doc contents.
- Group C requires Groups A and B in place to be meaningful — adversarial testing means testing robustness of a known baseline.

### Phase 4 Scope
Groups A and B. Group C deferred to Phase 5.

### Phase 5 Scope
Group C, plus any Group A/B items not completed in Phase 4.

### Revised Scope Decision — April 20, 2026

**Decision:** Decouple Phase 4 from publication-grade evaluation. Introduce two comparative evaluation layers for the paper.

**Phase 4 scope (reduced):**
- RAGAS evaluation only: Faithfulness, Retrieval Relevance, Answer Relevancy
- Golden dataset: 10-15 questions grounded in actual 5 policy docs (rebuild required — prior LLM-generated dataset had fabricated numbers like $5,000 cap, $150 meals that don't match actual Nova Pharma rules of $75,000 and $25/$50/$100)
- Medium article: focuses on RAGAS methodology, dataset construction lessons, and metric interpretation. Does NOT cover full 11-attribute framework.

**Phase 5 scope (expanded):**
- All previous Phase 5 items (SHAP correlation cleanup, Athena re-run, temporal splits, policy citation quality, LangGraph supervisor)
- NEW: Model comparison — Isolation Forest vs. Local Outlier Factor vs. One-Class SVM, same features and test set, metrics: precision/recall/F1 at critical-risk cohort (~6-8 hrs)
- Remaining 10 attribute evaluations at publication-grade rigor: Consistency, Reproducibility, Scope Adherence, Auditability, Groundedness, Graceful Failure, Robustness, Calibrated Confidence. Latency done at article-grade today; will re-run at paper-grade during Phase 5
- Publication-grade means: confidence intervals, literature citations for metric definitions, statistical significance testing, reproducibility package

**Paper scope (after Phase 5):**
- Full 11-attribute framework writeup
- Grounded in rigorous evaluations from Phase 5
- NEW: Framework comparison — 11-attribute Trustworthy AI framework vs. RAGAS-only evaluation, showing where each framework catches/misses failure modes for compliance AI (~4-6 hrs analytical work)
- Optional: alignment discussion with NIST AI RMF as governance-layer framing
- Venue, framing, and timeline to be scoped in a dedicated planning session

**Why this split:**
- Phase 4 ships as a complete artifact (RAGAS + Medium) without blocking on paper quality
- Paper work happens after Phase 5 when cognitive load is lower (post-medical treatment, during less-intense job search periods)
- Model comparison strengthens anomaly detection methodology defense in the paper
- Framework comparison strengthens the novelty claim for the 11-attribute framework
- Total additional work for both comparisons: ~10-14 hrs beyond previously scoped Phase 5 work

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
   - Second: bucket `compliance-athena-results` doesn't exist AND name is taken globally. NOTE (April 21, 2026): Resolved — the compliance-athena-results bucket is owned by another AWS account globally and was never needed. See commits `160feb9` + `fa2e924` for the fix. Real architecture uses `s3://compliance-risk-investigator/athena-query-output/` for query results.
   - Third: existing `compliance-risk-investigator` bucket is in us-east-1, Athena in us-east-2 — NOTE (April 21, 2026): the us-east-2 claim was wrong. Athena workgroup is in us-east-1. The misleading observation came from local AWS CLI being configured for us-east-2. Fixed in commit `160feb9` with `aws configure set region us-east-1`.
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
