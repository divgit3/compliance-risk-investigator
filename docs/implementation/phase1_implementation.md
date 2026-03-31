# Phase 1 Implementation Notes

---

## Task 1.6 — Synthetic Data Generator

### 1. Task Overview and Purpose

`synthetic_generator.py` generates realistic synthetic NovaPharma internal compliance data to simulate the field activity records that a real pharma company would maintain alongside its CMS Open Payments obligations.

The data serves two purposes:
1. **Anomaly detection training data** — realistic HCP interaction and speaker program records with organic compliance violations embedded through statistical distributions
2. **Model validation ground truth** — violation flags stored in the raw data but excluded from the detection pipeline, available only for post-hoc model evaluation

All data is clearly labeled synthetic. No real physician names, addresses, or proprietary company data are used.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `pipelines/ingest/synthetic_generator.py` | Main generator script |
| `models/marts/mart_hcp_interactions_features.sql` | Feature mart — violation flags excluded |
| `models/marts/mart_speaker_events_features.sql` | Feature mart — violation flags excluded |
| `models/marts/mart_attendees_features.sql` | Feature mart — violation flags excluded |
| `models/marts/mart_violation_ground_truth.sql` | Ground truth mart — violation flags only |

**Output datasets (S3):**

| Dataset | S3 Path | Records (Takeda run) |
|---|---|---|
| HCP Master | `synthetic/hcp_master/hcp_master.parquet` | 97,011 |
| HCP Interactions | `synthetic/hcp_interactions/hcp_interactions.parquet` | 993,986 |
| Speaker Events | `synthetic/speaker_programs/speaker_program_events.parquet` | 1,024 |
| Speaker Attendees | `synthetic/speaker_programs/speaker_program_attendees.parquet` | 9,801 |

**Key functions:**

| Function | Description |
|---|---|
| `load_cms_hcp_totals()` | Downloads CMS CSVs from S3 via multipart transfer, filters for target company, returns per-HCP annual totals. Uses per-year parquet cache to avoid re-downloading on reruns. |
| `_stream_year_filtered()` | Downloads one CMS year file via boto3 multipart transfer (100 MB parts, 5 concurrent), parses and filters in chunks, deletes temp file. |
| `generate_hcp_master()` | Creates HCP reference table with synthetic attributes and internal violation profiles |
| `generate_hcp_interactions()` | ~1M interaction records with Dirichlet-distributed payment amounts and per-meal-type cost limits |
| `generate_speaker_events()` | Speaker program events with venue/fee distributions and CMS-anchored fee caps |
| `generate_speaker_attendees()` | Attendee records per event |
| `apply_violation_flags()` | Post-generation rule checker — populates violation_types, severity, is_violation |
| `verify_reconciliation()` | Confirms internal payment sums reconcile to CMS totals within tolerance |
| `save_to_s3()` | Streams Parquet to S3 via BytesIO, no local disk writes |

---

### 3. Target Company: Takeda Pharmaceuticals

**Changed from Supernus to Takeda** (2026-03-31) to use a larger, more therapeutically diverse company with a richer CMS Open Payments footprint.

| | Supernus | Takeda |
|---|---|---|
| CMS HCP universe | ~4,000 | 97,011 |
| Therapeutic areas | Neurology/Epilepsy | GI, Oncology, Neuroscience, Rare Disease |
| Interaction records generated | ~30,000 | ~994,000 |
| CMS rows (2022–2024) | ~160K | ~475K |

The `TARGET_FILTER = "takeda"` string is used to filter the CMS manufacturer name column during streaming. The pseudonym `Nova Pharma Inc` is unchanged — it is a fictional company name used in all synthetic output.

---

### 4. Drug Name Mapping

Real Takeda drug names appear in CMS Open Payments data. They must never appear in synthetic output. `TAKEDA_DRUG_MAPPING` translates real names to fictional Nova Pharma brand names at generation time.

```python
TAKEDA_DRUG_MAPPING = {
    # Gastroenterology
    "ENTYVIO":    "Entavex",   "VONVENDI":  "Colvance",
    "GATTEX":     "Entavex",   "NATPARA":   "Colvance",
    # Neuroscience
    "TRINTELLIX": "Neurospan", "VYVANSE":   "Cognivex",
    "ADDERALL":   "Cognivex",
    # Oncology
    "NINLARO":    "Oncivance", "VELCADE":   "Hematrix",
    "ICLUSIG":    "Hematrix",  "ALUNBRIG":  "Oncivance",
    # Rare Disease
    "TAKHZYRO":   "Rarevance", "ADVATE":    "Orphagen",
    "ADYNOVATE":  "Orphagen",  "FEIBA":     "Rarevance",
    "HEMOFIL":    "Orphagen",
}

NOVA_PHARMA_PRODUCTS = sorted(set(TAKEDA_DRUG_MAPPING.values()))
# ['Cognivex', 'Colvance', 'Entavex', 'Hematrix',
#  'Neurospan', 'Oncivance', 'Orphagen', 'Rarevance']
```

Multiple real drug names can map to the same fictional name (e.g. ENTYVIO and GATTEX both → Entavex) where they represent the same therapeutic area or indication family. This collapses the product dimension to 8 fictional names while preserving therapeutic area diversity.

---

### 5. Technical Decisions and Why

**Decision: Multipart S3 download instead of streaming via get_object**

The original `s3.get_object()` approach held a single HTTP connection open for the entire ~8–9 GB 2024 file. AWS drops long-lived connections before completion, causing `IncompleteRead` errors after 15+ minutes of streaming. After 3 failed retry attempts the script would abort.

The fix: `s3.download_file()` with `TransferConfig(multipart_threshold=100MB, max_concurrency=5)`. boto3 splits the file into 100 MB parts, downloads them in parallel, and retries individual parts on failure — not the entire file. A connection drop at byte 3.7B no longer restarts from byte 0.

Trade-off: requires ~9 GB free disk space for the temp file during parsing. Temp file is deleted immediately after the CSV is parsed.

**Decision: Per-year parquet cache**

The CMS aggregation step (stream → filter → group by HCP/year) is expensive. A per-year cache at `data/processed/cms_year_cache/cms_{year}.parquet` means a failure on year 3 does not require re-downloading years 1 and 2. The final aggregated cache at `cms_hcp_totals_cache.parquet` is written only after all 3 years complete.

**Decision: Organic violations via profile-controlled distributions, not planted records**

Real compliance monitoring works by detecting anomalies in normal-looking data — not by finding explicitly labeled bad records. Planting labeled records would produce a dataset that looks nothing like production data. Instead, each HCP is assigned a `hcp_violation_profile` (clean/minor/moderate/serious) at creation time, and all downstream generation parameters (meal cost ranges, venue type probabilities, rationale vagueness, etc.) are controlled by that profile. Violations emerge naturally when rule thresholds are crossed.

**Decision: Dirichlet distribution for payment reconciliation**

The Dirichlet distribution splits a total into N parts that sum to exactly 1.0. Multiplying by the target CMS total gives N payment amounts that sum to within the reconciliation tolerance. This is more realistic than uniform random splits because it produces natural variation — some interactions are high-value, some are small — which mirrors real field activity patterns.

**Decision: Violation flags stored in data but excluded via dbt**

This is the "best of both worlds" architecture:
- Raw Parquet files contain ground truth (violation flags)
- dbt feature mart models SELECT all columns except violation flags → model input
- dbt `mart_violation_ground_truth` SELECT only violation flags + IDs → validation only
- The detection model never sees its own labels during training or inference

**Decision: Random seed = 42 everywhere**

Full reproducibility. Any team member running the script produces byte-identical output. This is essential for debugging anomaly detection models — you need to know whether a model change moved the needle, not whether data randomness did.

**Decision: FMV rate card is a hard ceiling — no tolerance band**

The previous design allowed a multiplier tolerance (e.g. clean HCPs could use up to 1.0× FMV, minor up to 1.1×). This was replaced with a hard ceiling: `speaker_fee > fmv_benchmark` is a violation regardless of profile. The rate card is the policy limit, not a guideline. Profiles control the distribution of multipliers used, so violation rates still vary by profile — serious profiles just generate fees that exceed the ceiling more often.

**Decision: Per-meal-type cost limits instead of flat $125**

PhRMA Code Section 2 sets a single per-person meal limit. The previous implementation used $125 flat. Updated to distinguish:
- Breakfast: $30/person
- Lunch: $75/person
- Dinner: $125/person

This produces a more realistic meal cost distribution (most interactions are lunch) and more granular violation signals. Meal type is assigned from `MEAL_TYPE_DISTRIBUTION` (10% breakfast, 70% lunch, 20% dinner) for all `interaction_type = "meal"` records. All other interaction types have `meal_type = NULL` and `meal_cost = NULL`.

**Decision: REPEAT_SAME_TOPIC_PROGRAMS replaces EXCESSIVE_SPEAKER_ENGAGEMENTS**

`EXCESSIVE_SPEAKER_ENGAGEMENTS` (spoke > 6 times/year) has no standalone industry policy basis — the annual compensation cap already limits total engagement volume. The OIG Special Fraud Alert (Nov 2020) specifically calls out repeated programs on the same or substantially same topic where no new medical/scientific developments exist as evidence of lack of genuine educational purpose. `REPEAT_SAME_TOPIC_PROGRAMS` (same HCP, same topic, > 3 times/year) is the more policy-grounded rule.

---

### 6. How It Works Step by Step

```
Step 1: load_cms_hcp_totals()
  → Check cms_hcp_totals_cache.parquet — return if found
  → For each year:
      → Check per-year cache (cms_year_cache/cms_{year}.parquet)
      → If not cached: multipart download to temp file, parse, delete temp
      → Save per-year cache
  → Aggregate: total payment per HCP per year
  → Save full cache
  → Return: one row per HCP with cms_total_{year} columns

Step 2: generate_hcp_master()
  → For each real CMS HCP ID, create synthetic HCP record
  → Assign: specialty (Takeda TAs), FMV tier, violation profile, practice ID
  → hcp_violation_profile is assigned here and used throughout
  → Output: hcp_master.parquet

Step 3: generate_hcp_interactions()
  → For each HCP × year: draw N interactions from profile range
  → Distribute CMS total across N payments via Dirichlet
  → Generate interaction attributes controlled by profile
  → For meal interactions: assign meal_type, apply per-type cost limit
  → 5% of serious HCPs get deliberate >10% reconciliation gap
  → Output: raw interactions with empty violation flag columns

Step 4: generate_speaker_events()
  → KOLs and non-clean HCPs become speakers
  → Skip year if cms_total_yr == 0 or < 2000
  → Cap speaker_fee to min(fmv_benchmark × multiplier, 40% of cms_total / n_events)
  → Track times_spoke_same_topic per HCP per topic per year
  → Output: raw events with empty violation flag columns

Step 5: generate_speaker_attendees()
  → For each event: sample HCPs from master as attendees
  → Attendee type (hcp/staff/family) controlled by profile
  → Track same-topic attendance counts across events
  → Output: raw attendees with empty violation flag columns

Step 6: apply_violation_flags() × 3
  → Run rule checker against each dataset
  → Populate violation_types, violation_severity, is_violation
  → Rules map directly to PhRMA/OIG policy thresholds

Step 7: verify_reconciliation()
  → Compare internal payment sums to CMS totals per HCP/year
  → Report % perfect, minor gap, major gap

Step 8: save_to_s3() × 4
  → Serialize each DataFrame to Parquet via BytesIO
  → Upload to S3 synthetic/ prefix
```

---

### 7. How to Run and Verify

**Prerequisites:**
```bash
venv/bin/pip install faker loguru boto3 numpy pandas pyarrow python-dotenv
```

**Run:**
```bash
venv/bin/python pipelines/ingest/synthetic_generator.py
```

Note: use `venv/bin/python` directly — `python` may alias to system Python on some machines.

**First run:** Downloads ~23 GB from S3 (multipart, ~20 min). Builds per-year cache files.
**Subsequent runs:** Loads from `data/processed/cms_hcp_totals_cache.parquet` in <2 seconds.
**To force refresh:** Delete `data/processed/cms_hcp_totals_cache.parquet` (and optionally `data/processed/cms_year_cache/`).

**Verify S3 output:**
```bash
python3 -c "
import boto3
s3 = boto3.client('s3', region_name='us-east-1')
resp = s3.list_objects_v2(Bucket='compliance-risk-investigator', Prefix='synthetic/')
for obj in resp.get('Contents', []):
    print(f\"{obj['Key']}  ({obj['Size']/1024**2:.1f} MB)\")
"
```

---

### 8. HCP Specialty Distribution (Takeda Therapeutic Areas)

```python
SPECIALTY_DISTRIBUTION = {
    "Gastroenterology": 0.30,   # Entyvio, Gattex — largest TA
    "Oncology":         0.25,   # Ninlaro, Velcade, Iclusig, Alunbrig
    "Neurology":        0.20,   # Trintellix, Vyvanse
    "Rare Disease":     0.15,   # Takhzyro, Advate, Feiba
    "Primary Care":     0.10,   # General prescribers
}
```

---

### 9. FMV Rate Card

Rate card defines the per-engagement ceiling Nova Pharma will pay HCPs for speaker and consulting engagements. The rate IS the ceiling — no tolerance band. `speaker_fee > fmv_benchmark` is flagged as `SPEAKER_FEE_EXCEEDS_FMV` regardless of magnitude.

| Specialty | Local | Regional | National |
|---|---|---|---|
| Gastroenterology | $1,000 | $2,000 | $3,500 |
| Neurology | $1,000 | $2,000 | $3,500 |
| Oncology | $1,200 | $2,500 | $4,500 |
| Rare Disease | $1,500 | $3,000 | $5,500 |
| Primary Care | $750 | $1,500 | $2,500 |
| Other | $750 | $1,500 | $2,500 |

**Geographic tiers reflect scope of work:**
- Local: single-market engagements, 1–2 hour commitment
- Regional: multi-state engagements, higher prep/travel burden
- National: major congresses, advisory boards, publications

**Company-wide $75,000 annual compensation cap:**
Regardless of specialty or tier, no single HCP may receive more than $75,000 total annually in speaker fees, consulting fees, advisory board fees, or training fees. Travel reimbursements and meal costs are excluded from this cap (consistent with PhRMA guidance).

---

### 10. Policy Grounding for Each Violation Type

| # | Type | Policy Source | Flag Logic |
|---|---|---|---|
| 1 | MEAL_COST_EXCESSIVE | PhRMA Code 2022, Section 2 | meal_cost > MEAL_LIMITS[meal_type] (breakfast $30, lunch $75, dinner $125) |
| 2 | SPEAKER_VENUE_INAPPROPRIATE | OIG Special Fraud Alert Nov 2020 | venue_type in {restaurant, entertainment_venue, luxury_resort, sports_venue} |
| 3 | REPEAT_PROGRAM_ATTENDANCE | PhRMA Code 2022, Section 7 | times_attended_same_topic ≥ 3 within year |
| 4 | SPEAKER_FEE_EXCEEDS_FMV | OIG Special Fraud Alert Nov 2020 | speaker_fee > fmv_benchmark (hard ceiling, no tolerance) |
| 5 | SPEAKER_SELECTED_BY_PRESCRIBING | PhRMA Code 2022, Section 7 | times_spoke > 4 AND annual_comp > 4× FMV benchmark |
| 6 | LOW_ATTENDEE_COUNT | OIG Special Fraud Alert Nov 2020 | attendee_count < 3 |
| 7 | REPEAT_SAME_TOPIC_PROGRAMS | OIG Special Fraud Alert Nov 2020 | times_spoke_same_topic > 3 within year |
| 8 | ALCOHOL_PROVIDED | OIG 2020 + PhRMA Code (explicit prohibition) | alcohol_provided = true |
| 9 | VAGUE_BUSINESS_RATIONALE | OIG Special Fraud Alert Nov 2020 | rationale empty, generic, or < 15 characters |
| 10 | RAPID_INTERACTION_PATTERN | AKS general guidance / OIG | same hcp_id + rep_id > 3 interactions in same week |
| 11 | CMS_RECONCILIATION_GAP | Sunshine Act reporting requirements | ABS(cms_total - internal_sum) / cms_total > 10% |
| 12 | NON_HCP_ATTENDEES | OIG Special Fraud Alert Nov 2020 | attendee_type in {family, staff, non_prescriber, unknown} |
| 13 | ANNUAL_COMPENSATION_CAP_EXCEEDED | PhRMA Code 2022, Section 7 + Nova Pharma policy | annual_total_ytd > $75,000 |
| 14 | ATTENDEE_SAME_OFFICE_AS_SPEAKER | OIG 2020 + PhRMA Code | same_office_as_speaker = true |

**Note on Type 7:** `REPEAT_SAME_TOPIC_PROGRAMS` replaced `EXCESSIVE_SPEAKER_ENGAGEMENTS` (was: times_spoke_this_year > 6). The OIG 2020 alert specifically flags repeated programs on substantially the same topic as lacking genuine educational purpose. The annual compensation cap (Type 13) already limits total volume without a standalone engagement count rule.

**Policy sources:**
- PhRMA Code on Interactions with Healthcare Professionals (2022 edition)
- OIG Special Fraud Alert: Speaker Programs (November 2020)
- Anti-Kickback Statute (AKS) general guidance
- Physician Payments Sunshine Act / CMS Open Payments reporting requirements
- Nova Pharma Internal Policy (fictional, modeled on PhRMA Code Section 7)

---

### 11. How Violation Flags Work and Why They Are Excluded from the Detection Pipeline

**Architecture:**

```
synthetic_generator.py
  ↓ generates records with organic violations
  ↓ apply_violation_flags() evaluates rules → populates 3 columns
  ↓ save_to_s3() writes full records including violation flags

S3 (raw parquet — contains violation flags)
  ↓
dbt staging models (pass through all columns)
  ↓
dbt mart_*_features.sql          dbt mart_violation_ground_truth.sql
  SELECT all EXCEPT               SELECT ONLY
  violation_types,                record_id, hcp_id, program_year,
  violation_severity,             record_type, violation_types,
  is_violation                    violation_severity, is_violation
  ↓                               ↓
Phase 2 anomaly detection         Phase 2 model validation only
model input                       (precision / recall / F1 scoring)
```

---

### 12. Competitor Mapping (company_mapping.csv)

Competitors are filtered from CMS data using substring matches on the manufacturer name column. Both `squibb` and `celgene` map to `Halcyon Pharma Inc` because E.R. Squibb & Sons and Celgene are both Bristol Myers Squibb entities in CMS Open Payments.

| CMS Pattern | Pseudo Name | Role |
|---|---|---|
| takeda | Nova Pharma Inc | Target |
| janssen | Stratos Biosciences | Competitor |
| merck | Nexagen Sciences | Competitor |
| amgen | Pinnacle Biosciences | Competitor |
| squibb | Halcyon Pharma Inc | Competitor (BMS) |
| celgene | Halcyon Pharma Inc | Competitor (BMS) |

---

### 13. Known Limitations

1. **Speaker events undercounting** — The `cms_total_yr < 2000` guard (intended to prevent unrealistically tiny speaker fees) cuts too aggressively for Takeda's broad HCP universe. Generated 1,024 events vs 5,000+ target. Threshold should be lowered to `< 500` or removed and replaced with fee-cap-only logic.

2. **Attendees dataset uses stg_synthetic_speaker_programs as source** — the attendees staging model currently points to the same source as speaker events. In Task 1.7/1.8 this should be split into separate sources once the Glue crawler catalogs the attendees Parquet separately.

3. **Dirichlet distribution produces some near-zero payments** — a small number of synthetic interaction payments will be < $1. These are technically valid but may look unusual. A minimum payment floor (e.g. $10) could be added in a future iteration.

4. **CMS reconciliation only validates against Takeda rows** — the reconciliation check compares internal sums to Takeda-filtered CMS totals. If CMS has partial-year data for 2024, reconciliation percentages will appear lower than expected.

5. **hcp_violation_profile stored in hcp_master** — the profile column is in hcp_master.parquet. Care must be taken in Phase 2 to ensure this column is never joined into the feature mart pipeline.

6. **apply_violation_flags uses positional index dict** — the `vt` dict is keyed by `range(len(df))` which assumes the DataFrame index is a clean 0-to-N range. This holds for freshly generated DataFrames but would break if the function received a filtered/reindexed DataFrame.

---

### 14. Next Steps

- **Task 1.7** ✓ — Run `synthetic_generator.py` and verify output record counts and violation distributions (completed 2026-03-31)
- **Task 1.8** — Run Glue crawler on `synthetic/` prefix to catalog the 4 new Parquet files
- **Task 1.8** — Wire dbt to Athena (switch from dbt-duckdb to dbt-athena for production queries against S3/Glue)
- **Task 1.8** — Update `sources.yml` to include synthetic data sources
- **Task 1.9** — Add dbt tests (not_null, accepted_values, referential integrity between master and interaction tables)
- **Tuning** — Lower or remove `cms_total_yr < 2000` guard to bring speaker events closer to 5,000+ target
- **Phase 2** — Build anomaly detection model using `mart_*_features` as input, validate against `mart_violation_ground_truth`
