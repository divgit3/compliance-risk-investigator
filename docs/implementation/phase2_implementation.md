# Phase 2 Implementation Notes

---

## Task 2.0a — embed_policy_docs.py

### 1. Task Overview and Purpose

`embed_policy_docs.py` is the prerequisite for the entire Phase 2 AI explanation layer. It ingests 5 policy PDFs from S3, converts them into overlapping text chunks, embeds them via OpenAI `text-embedding-ada-002`, and upserts the resulting vectors into the Qdrant `policy_docs` collection.

Without this pipeline, the RAG layer has no knowledge base to query. Every downstream component that needs to explain a compliance flag — "why is this speaker fee suspicious?" — retrieves grounding context from this collection before generating a response.

**How it fits into Phase 2:**
- **Task 2.0b** (`business_rules_registry.py`): RAGs against this collection to extract concrete rule thresholds (e.g., "$4,000 FMV ceiling") and writes them to `rules.json`
- **Tasks 2.2+**: All dbt model business rule constants are sourced from `rules.json` rather than being hard-coded
- **Phase 3 Policy Agent**: Uses the same Qdrant collection for natural language compliance Q&A ("What does PhRMA say about meal limits?")

**Role of Qdrant as policy knowledge base:**
Qdrant stores each chunk as a 1536-dimensional dense vector alongside its full metadata payload. At query time, a compliance question is embedded and the nearest-neighbor chunks are retrieved — giving the LLM precise policy text to reason over, rather than relying on parametric memory.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `pipelines/embed_policy_docs.py` | Main pipeline script |
| `requirements.txt` | Added `pymupdf` dependency |

**5 Functions:**

| Function | Purpose |
|---|---|
| `download_pdf_from_s3(bucket, key)` | Downloads raw PDF bytes from S3 via boto3; logs filename and size |
| `extract_text_from_pdf(pdf_bytes, filename)` | Extracts text page-by-page via PyMuPDF; skips pages < 50 chars; returns list of `{page_num, text, filename}` |
| `chunk_text(pages, filename, doc_metadata)` | Flattens all page text into word list, slides CHUNK_SIZE/CHUNK_OVERLAP window; returns list of chunk dicts with full metadata |
| `embed_chunks(chunks)` | Batches chunks in groups of 100, calls OpenAI embeddings API with exponential backoff; adds `embedding` key to each chunk dict |
| `upsert_to_qdrant(chunks_with_embeddings)` | Batches in groups of 50, upserts PointStructs to Qdrant; verifies collection exists first; returns total points upserted |

**Supporting functions:**

| Function | Purpose |
|---|---|
| `verify_qdrant_collection()` | Queries Qdrant for point count, vector dim, and collection status |
| `_chunk_id_to_qdrant_id(chunk_id)` | Converts string chunk_id to stable MD5-based integer point ID |

**Chunk schema (all fields):**

| Field | Type | Description |
|---|---|---|
| `chunk_id` | str | `{doc_id}_chunk_{index:04d}` — unique per chunk |
| `doc_id` | str | `DOC_001` through `DOC_005` |
| `doc_type` | str | `cms_reference`, `company_policy`, `regulatory_guidance`, `fraud_alert`, `industry_code` |
| `authority` | str | `CMS`, `Nova Pharma`, `OIG`, `PhRMA` |
| `filename` | str | Original PDF filename |
| `page_num` | int | Page where chunk starts (1-indexed) |
| `chunk_index` | int | 0-indexed position within document |
| `text` | str | Raw chunk text (~512 words) |
| `char_count` | int | Length of `text` in characters |
| `relevant_rules` | list[str] | Pre-assigned rule tags for this document |
| `embedding` | list[float] | 1536-dim vector (stored in Qdrant, not in payload) |

**Qdrant point structure:**

```
PointStruct(
    id      = int(md5(chunk_id)[:8], 16),  # stable 32-bit integer
    vector  = [float, ...],                 # 1536-dim
    payload = {all chunk fields except embedding}
)
```

---

### 3. Technical Decisions and Why

**PyMuPDF over other PDF libraries:**
PyMuPDF (`fitz`) is significantly faster and more accurate than PyPDF2 for text extraction, particularly for PDFs with complex layouts. The regulatory PDFs (OIG CPG, PhRMA Code) contain multi-column layouts and headers/footers that PyPDF2 often merges incorrectly. PyMuPDF's `get_text("text")` produces clean per-page output. PyPDF2 is retained in requirements.txt for backward compatibility with `policy_doc_loader.py`.

**Word-based chunking over tiktoken:**
tiktoken adds a dependency on OpenAI's tokeniser which requires a separate install and has version drift issues. Word-based chunking is a well-understood approximation: 512 words ≈ 640 tokens for English regulatory text (avg. ~1.25 tokens/word). This is comfortably below the 8,192-token `text-embedding-ada-002` context limit. The simpler implementation is also easier to audit.

**Chunk size 512 words / overlap 64 words:**
512 words (~640 tokens) gives enough context for a policy clause to be self-contained while staying well under the embedding model limit. 64-word overlap (12.5%) ensures that a rule clause split across a chunk boundary appears in full in at least one chunk — important for rules stated across two or three sentences.

**Batch size 100 for embeddings, 50 for Qdrant:**
OpenAI's embedding API accepts up to 2,048 inputs per request, but 100 is a safe upper bound that avoids timeout risk for long chunks. Qdrant's Python client recommends batches of 64-100 for `upsert`; 50 is conservative to keep individual request latency predictable.

**MD5 hash for Qdrant integer point IDs:**
Qdrant requires integer or UUID point IDs. String IDs (`DOC_001_chunk_0042`) aren't supported. MD5 of the chunk_id string, truncated to 8 hex chars (32-bit integer), gives a stable, deterministic, collision-resistant mapping. The same chunk always maps to the same Qdrant ID, making re-runs idempotent (upsert overwrites by ID).

**Exponential backoff on embedding calls:**
OpenAI's rate limits can trigger on bursts of large-batch requests. Two retries with 2s → 4s delays handle transient 429s without requiring manual intervention. Three attempts is sufficient for a pipeline that runs once — this is not a high-throughput production system.

---

### 4. Document Metadata Design

**Why `authority` and `doc_type` fields:**
Different authorities carry different enforcement weight. OIG guidance and fraud alerts represent formal government regulatory positions. PhRMA Code is industry self-regulation. Nova Pharma internal policy may be stricter than either. The `authority` field lets the business rules registry (`business_rules_registry.py`, Task 2.0b) apply precedence logic: if OIG and PhRMA disagree on a threshold, OIG wins. If Nova Pharma is stricter than both, Nova Pharma wins.

`doc_type` enables filtering by document category — a query for "what counts as a compliance violation in speaker programs" should prioritise `fraud_alert` and `regulatory_guidance` over `cms_reference` (which is a data dictionary, not a rules document).

**Why `relevant_rules` tags are pre-assigned:**
The tags (`meal_limits`, `fmv`, `speaker_programs`, etc.) allow the rules registry to issue targeted queries — "retrieve chunks tagged `meal_limits` from documents with authority `OIG` or `PhRMA`" — rather than relying on semantic search alone. This improves precision for structured rule extraction where exact thresholds must be found.

**How `authority` drives stricter-rule logic in 2.0b:**
Priority order (most to least authoritative):
1. `OIG` fraud alerts and CPGs — government enforcement position
2. `CMS` — reporting requirements
3. `PhRMA` — industry code (strong but self-regulatory)
4. `Nova Pharma` — internal policy (may be stricter; always enforced internally)

When multiple documents mention the same rule threshold differently, the rules registry selects the strictest value among authoritative sources.

---

### 5. How to Run and Verify

**Run:**
```bash
python pipelines/embed_policy_docs.py
```

**Expected output:**
```
Docs processed:        5/5
Total chunks embedded: ~171
Total points upserted: ~171
Collection status:     green
Time taken:            ~30-60s (dominated by OpenAI API calls)
```

**Verify Qdrant collection:**
```bash
curl http://localhost:6333/collections/policy_docs | python3 -m json.tool
# Look for: "points_count": 171, "status": "green"
```

**Verify a sample payload:**
```bash
curl -X POST http://localhost:6333/collections/policy_docs/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit": 2, "with_payload": true, "with_vector": false}' \
  | python3 -m json.tool
```

**Re-run behaviour:**
If the collection already has points, the script prompts:
```
Re-embed and overwrite? [y/N]:
```
Entering `y` proceeds; upsert overwrites existing points by ID (idempotent).
Any other input aborts safely.

---

### 6. Known Limitations

1. **`nova_pharma_internal_policy_SYNTHETIC.pdf` is synthetic** — the document was generated by `policy_doc_loader.py` with placeholder text. Rule thresholds in it (e.g., "$75K annual cap") are correct by design, but the surrounding context may not match the prose style of a real compliance policy. The rules registry should weight OIG/PhRMA sources more heavily for threshold extraction.

2. **`cms_open_payments_data_dictionary.pdf` contains field definitions, not rules** — this document explains what CMS columns mean (e.g., "Nature of Payment"), not what is permissible. It has low rule-extraction signal. It is embedded because the RAG layer may need it to answer questions about CMS data interpretation.

3. **Word-based chunking may split mid-sentence** — sentences longer than the step size (448 words) will be split. In regulatory PDFs, very long sentences are rare, but tables and lists may be fragmented. The 64-word overlap mitigates this for most rule clauses.

4. **No deduplication across runs** — if run twice, chunks are upserted by the same MD5-derived IDs (idempotent overwrite). The collection will not grow, but embeddings are re-computed and re-upserted, consuming OpenAI API credits. The `existing_points > 0` guard prevents accidental re-runs.

5. **Single-threaded** — documents are processed sequentially. With 5 documents and ~171 chunks, total runtime is ~30-60 seconds (dominated by OpenAI API calls). Parallelism is not needed at this scale.

---

### 7. Next Steps

- **Task 2.0b** (`business_rules_registry.py`): Uses this collection to RAG-extract concrete thresholds for each rule tag and reconcile across authorities. Output: `rules.json` — single source of truth for all business rule constants used in Tasks 2.2+
- **Tasks 2.2+**: dbt models read rule thresholds from `rules.json` via dbt variables rather than hard-coded constants
- **Phase 3 Policy Agent**: Uses the same `policy_docs` Qdrant collection for natural language policy Q&A via LangChain retrieval chain

---

## Task 2.1 — mart_hcp_spend_features

### 1. Task Overview and Purpose

`mart_hcp_spend_features` is the first Phase 2 dbt mart. It produces **one ML-ready row per HCP** aggregating all CMS Open Payments (Nova Pharma / Takeda) external spend signals from 2022-2024.

This mart is the primary input to the Phase 2 anomaly detection pipeline for the "external spend" signal category. The goal is to quantify how much money Nova Pharma paid each HCP via CMS-reportable channels, identify patterns that exceed regulatory guidelines, and score each HCP with a pre-ML heuristic risk score.

Violation flags are deliberately excluded to prevent label leakage — this table feeds the unsupervised anomaly detector, not the ground-truth evaluator.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `pipelines/dbt_project/models/marts/mart_hcp_spend_features.sql` | Main mart SQL — 6 CTEs, 32 output columns |

**Output:** `compliance_risk_raw.mart_hcp_spend_features` in Athena (materialized table)

**Row count:** 97,011 (one per HCP)

**Key output columns (32 total):**

| Group | Columns |
|---|---|
| Volume | `lifetime_total_spend`, `lifetime_payment_count`, `spend_2022/2023/2024`, `peak_year_spend`, `active_payment_years` |
| Annual cap | `annual_cap_pct_used`, `at_cap_flag`, `near_cap_flag` |
| Meal limits | `meals_over_limit_count`, `meal_breach_rate`, `max_meal_overage_pct` |
| YoY trend | `yoy_growth_2223`, `yoy_growth_2324`, `multi_year_increasing_flag` |
| Payment mix | `pct_food_beverage`, `pct_speaking_fee`, `pct_consulting`, `speaking_fee_total`, `speaking_fee_count`, `consulting_fee_total`, `food_beverage_total` |
| Rep concentration | `avg_unique_reps`, `max_unique_reps`, `top_rep_concentration_pct` |
| Composite | `raw_spend_risk_score` (0-100), `has_cms_payments`, `mart_created_at` |

---

### 3. Technical Decisions and Why

**Decision: `CAST(NOW() AS TIMESTAMP)` instead of `CURRENT_TIMESTAMP`**

`CURRENT_TIMESTAMP` returns `timestamp(3) with time zone` in Athena/Presto, which is not a supported Hive table storage type. `CAST(NOW() AS TIMESTAMP)` produces a plain `timestamp` that Glue/Hive accepts.

**Decision: `yoy_growth_2223` / `yoy_growth_2324` are intentionally nullable**

`NULL` means "HCP had no spend in the base year." Treating it as `0.0` would falsely imply "no growth" for HCPs who simply weren't paid in 2022 or 2023. The ML pipeline must handle these NULLs via imputation; they carry different information than a 0% growth rate.

**Decision: target-conditional CTEs for synthetic data references**

`stg_synthetic_interactions` is a DuckDB-only source (httpfs Parquet view). It doesn't exist as a Glue table. Two CTEs depend on it:
- `hcp_master` (97K HCP spine): on Athena, derived from `mart_target_payments` instead
- `rep_agg` (rep concentration): on Athena, returns 0-filled rows

This means on Athena the output excludes HCPs with no CMS payments (as they aren't in `mart_target_payments`). On Athena, all 97,011 output rows have `has_cms_payments = true`. This is documented as a known limitation.

**Decision: $125 dinner ceiling for all CMS Food & Beverage records**

CMS doesn't include meal type (breakfast/lunch/dinner) in its reporting. The PhRMA Code specifies three tiers: $30 breakfast, $75 lunch, $125 dinner. Without meal type, applying the lowest threshold ($30) would produce misleading breach counts for normal dinners. The dinner ceiling ($125) is used because:
1. Pharma dinners are the most common meal format in CMS F&B records
2. It is the most defensible per-transaction limit
3. Exceeding it is a genuine signal regardless of meal time

**Decision: speaking-fee detection via `LOWER(nature_of_payment) LIKE '%speaker%' OR LIKE '%faculty%'`**

The actual CMS value is: `"Compensation for services other than consulting, including serving as faculty or as a speaker at a venue other than a continuing education program"`. This string is unwieldy; the LIKE pattern captures it robustly and is future-proof if CMS renames the category.

**Decision: `GREATEST()` for `peak_year_spend` instead of a window function**

The per-year spend values are already pivoted to `spend_2022/2023/2024` as columns. `GREATEST()` is simpler and Athena-compatible. Window functions would require unpivoting first.

---

### 4. How It Works — Step by Step

```
mart_target_payments (473K rows)
        │
        ▼
[hcp_master]          [cms_payments]
97K distinct HCPs     Classify each row:
(Athena: from CMS)    is_meal, is_speaking_fee,
                      is_consulting, meal_over_limit,
                      meal_overage_pct
        │                    │
        │                    ▼
        │            [hcp_year_agg]
        │            SUM/COUNT by (hcp_id, program_year)
        │                    │
        │                    ▼
        │            [hcp_cross_year]
        │            Pivot 3 years → 1 row per HCP
        │            GREATEST() for peak_year_spend
        │
        │            [rep_agg]
        │            (Athena: 0-filled — no rep_id in CMS)
        │            (DuckDB: from stg_synthetic_interactions)
        │
        │                    │
        ▼                    ▼
[hcp_features]
JOIN hcp_cross_year + rep_agg
Compute ratios (meal_breach_rate, pct_*)
Compute flags (at_cap_flag, multi_year_increasing_flag)
Compute raw_spend_risk_score (0-100 heuristic)
        │
        ▼
[final]
LEFT JOIN hcp_master → hcp_features
COALESCE all numerics → 0
yoy_growth_* left nullable (NULL ≠ 0)
```

---

### 5. How to Run and Verify

**Run:**
```bash
cd pipelines/dbt_project
dbt run --target athena --select mart_hcp_spend_features
```

**Expected output:**
```
1 of 1 OK created sql table model compliance_risk_raw.mart_hcp_spend_features
[OK 97011] in ~9s
```

**Verify shape and sanity (Athena query):**
```sql
SELECT
  COUNT(*)                                                   AS total_hcps,
  SUM(CASE WHEN has_cms_payments    THEN 1 END)              AS with_cms_payments,
  SUM(CASE WHEN at_cap_flag         THEN 1 END)              AS at_annual_cap,
  SUM(CASE WHEN near_cap_flag       THEN 1 END)              AS near_annual_cap,
  SUM(CASE WHEN meals_over_limit_count > 0 THEN 1 END)       AS with_meal_breaches,
  SUM(CASE WHEN multi_year_increasing_flag THEN 1 END)       AS escalating_3yr,
  ROUND(AVG(raw_spend_risk_score), 2)                        AS avg_risk_score,
  ROUND(MAX(raw_spend_risk_score), 2)                        AS max_risk_score,
  ROUND(MAX(lifetime_total_spend), 2)                        AS max_lifetime_spend
FROM compliance_risk_raw.mart_hcp_spend_features;
```

**Observed results (2026-04-02):**
```
total_hcps:        97,011
with_cms_payments: 97,011
at_annual_cap:         43
near_annual_cap:       67
with_meal_breaches:   236
escalating_3yr:     4,051
avg_risk_score:      0.67
max_risk_score:     81.16
max_lifetime_spend: $665,624.92
```

**Sanity checks:**
- `at_cap_flag` count (43) is a small fraction of total — expected; $75K annual cap is high for most HCPs
- `avg_risk_score` of 0.67 is low — most HCPs receive small F&B payments only, which score near 0
- `max_lifetime_spend` of $665K over 3 years is plausible for a national KOL speaker
- `escalating_3yr` (4,051) represents ~4% of HCPs — reasonable signal prevalence

---

### 6. Business Rules Applied

| Rule | Source | Implementation |
|---|---|---|
| Meal limit — breakfast | PhRMA Code 2022, §3 | $30 threshold (not applied — no meal type in CMS) |
| Meal limit — lunch | PhRMA Code 2022, §3 | $75 threshold (not applied — no meal type in CMS) |
| Meal limit — dinner | PhRMA Code 2022, §3 | $125 threshold applied to all F&B records |
| Annual compensation cap | OIG CPG / internal policy | $75,000 per HCP per year; `at_cap_flag` when ≥ $75K |
| Near-cap warning | Internal policy | $60,000 threshold; `near_cap_flag` |
| FMV | Tracked via `pct_speaking_fee` | Zero-tolerance overage flagged upstream in synthetic data; CMS mix used as proxy |
| Violation exclusion | ML design (no label leakage) | No violation flags in output |

---

### 7. Guardrails Applied

- **No violation flags in output** — `mart_violation_ground_truth` holds labels; this mart is ML input only
- **All numerics COALESCE to 0** — ML models receive no nulls except intentional `yoy_growth_*` columns
- **`yoy_growth_*` left nullable** — `NULL` semantically distinct from `0.0`; imputation handled in feature pipeline
- **`has_cms_payments` boolean** — allows downstream models to distinguish "zero CMS payments" from "HCP not in CMS at all"
- **`CAST(meal_breach_rate numerator AS DOUBLE)`** — prevents integer division truncation in Athena
- **`NULLIF(SUM(rep_count), 0)`** in `top_rep_share` — division guard

---

### 8. Known Limitations

1. **Partial HCP spine on Athena** — `hcp_master` is derived from `mart_target_payments` on Athena (CMS HCPs only). The full 97K spine requires synthetic data registered as a Glue table. HCPs with no CMS payments don't appear in the Athena output. On DuckDB, the full 97K spine is used.

2. **Rep concentration is 0 on Athena** — `avg_unique_reps`, `max_unique_reps`, `top_rep_concentration_pct` are all 0 on Athena. These are populated correctly on DuckDB via `stg_synthetic_interactions`. Resolution: register synthetic parquet in Glue (future task).

3. **No meal-type distinction in CMS** — The $125 dinner ceiling is applied uniformly to all F&B records. Breakfast ($30) and lunch ($75) thresholds from PhRMA Code cannot be enforced without meal-type data. This means some breakfast/lunch records exceeding their lower thresholds are not flagged.

4. **CMS F&B is total amount, not per-person** — CMS reports the total amount paid, not per-attendee cost. A $200 F&B payment for 3 attendees (~$67/person) would be flagged as a breach even though per-person cost is within limits. Attendee count is not available in CMS records.

5. **`raw_spend_risk_score` is a heuristic** — The 0-100 score uses manually tuned weights and is not calibrated against actual violations. It is intended for ranking and exploratory analysis only; the Isolation Forest model in Task 2.9 produces the authoritative anomaly score.

6. **2022 CMS `record_id` is NULL** — Known Phase 1 ingest issue (bigint type mismatch). Does not affect any feature in this mart (no dependency on `record_id`).

---

### 9. Next Steps

- **Task 2.2**: `mart_event_features` — event-level features (interaction type, meal cost per attendee, FMV tier, venue, alcohol flag)
- **Task 2.3**: `mart_hcp_risk_profile` — join external spend features with internal interaction features into unified HCP risk profile
- **Future**: Register synthetic HCP Parquet in Glue to enable full 97K spine and rep concentration features on Athena
- **Future**: Use synthetic `meal_type` column to apply per-meal-type thresholds ($30/$75/$125) in the internal interaction features mart
