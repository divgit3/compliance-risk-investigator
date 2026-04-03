# Phase 2 Implementation Notes

---

## Task 2.7 — feature_store.py

### 1. Task Overview and Purpose

`feature_store.py` is the central feature store — the single source of truth for all downstream ML tasks (Tasks 2.8–2.12). It:

1. **Merges all feature matrices** into a single 97,011-row DataFrame
2. **Resolves the Athena/DuckDB split** that was a known limitation since Tasks 2.3 and 2.4 — by recomputing benchmark signals in Python now that Athena spend data is available in memory
3. **Separates ground truth** into its own parquet file so it can never accidentally contaminate the ML feature matrix

**Why it exists as a separate layer from the dbt marts:**
The dbt marts are split across two engines (Athena for CMS data, DuckDB for synthetic data) and cannot cross-join. Once all data is loaded into Python memory, `feature_store.py` can:
- Merge across the engine boundary on `hcp_id`
- Recompute benchmark signals using real Athena spend data (unavailable to the DuckDB mart)
- Apply type coercions and null fills that belong in the Python ML pipeline, not SQL

**How it resolves the Athena/DuckDB split:**
`mart_benchmark` on DuckDB has 0-filled spend and benchmark columns because `mart_hcp_spend_features` is Athena-only. Once `hcp_spend_feature_matrix.parquet` (Athena data) is loaded into Python, `compute_real_benchmarks()` uses the real CMS spend values to compute percentile ranks, cap patterns, and engagement priority scores that were impossible in the dbt layer.

**Why ground truth is kept separate:**
`mart_hcp_risk_profile` carries `ground_truth_violation_count` and `ground_truth_max_severity` — synthetic violation labels used for model validation. Including these in the feature matrix would be label leakage (the ML model would learn to predict what it's supposed to detect). The ground truth parquet is the only file that `test_anomaly_models.py` reads from.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `features/feature_store.py` | Central feature store — merge, recompute, validate, save |

**9 functions:**

| Function | What it does |
|---|---|
| `load_spend_matrix()` | Loads `hcp_spend_feature_matrix.parquet` (Athena, 97,011 rows, scaled). Raises `FileNotFoundError` if not present |
| `load_event_matrix()` | Loads `event_feature_matrix.parquet` (~1,354 speaker rows). Raises `FileNotFoundError` if not present |
| `load_risk_profile()` | Reads `mart_hcp_risk_profile` from DuckDB — interaction features + ground truth. Selects only columns not already in spend_matrix |
| `load_benchmark_context()` | Reads `mart_benchmark` from DuckDB — engagement/outlier signals + `spend_2022/2023/2024` as `_raw` aliases for recompute |
| `merge_all(spend_df, event_df, risk_df, benchmark_df)` | 4-way LEFT JOIN on `hcp_id`. Spend matrix is the 97,011-row spine. Non-speaker HCPs get NaN→0 for event columns |
| `compute_real_benchmarks(df)` | 7-step benchmark recompute using real Athena spend data. Adds `_real` suffix columns |
| `extract_ground_truth(df)` | Extracts `hcp_id + ground_truth_* + has_violation` to separate DataFrame. **VALIDATION ONLY** |
| `build_feature_matrix(df)` | Drops `EXCLUDE_FROM_FEATURES`, string columns, GT columns. Fills nulls. Encodes booleans as int |
| `validate_feature_store(feature_df, gt_df)` | 8 checks including row count, no nulls, no strings, violation rate in [20%, 30%], no GT columns in feature matrix |
| `save_outputs(...)` | Saves 3 files, returns paths dict |

**Output files:**

| File | Rows | Consumers |
|---|---|---|
| `features/outputs/feature_store.parquet` | 97,011 | Task 2.8 (rule_based_flags.py), Task 2.9 (Isolation Forest) |
| `features/outputs/ground_truth_labels.parquet` | 97,011 | Task 2.12 (test_anomaly_models.py) only |
| `features/outputs/feature_store_metadata.json` | — | Metadata: merge stats, benchmark recompute stats, feature column list |

**Merge strategy (join order):**
```
spend_matrix (97,011 rows — Athena HCP spine)
  LEFT JOIN event_matrix    (~1,354 rows → 0-fill for 95,657 non-speakers)
  LEFT JOIN risk_profile    (97,011 rows — interaction features + GT)
  LEFT JOIN benchmark       (97,011 rows — engagement signals + _raw spend)
```

---

### 3. Technical Decisions and Why

**LEFT JOIN from spend matrix spine:**
`hcp_spend_feature_matrix.parquet` comes from Athena and contains all HCPs Nova Pharma has ever paid. This is the correct spine — it represents the full population under compliance scrutiny. Using risk_profile or benchmark as spine would also work on DuckDB dev (both have 97,011 rows), but the Athena spend matrix is the authoritative HCP list for the compliance use case.

**`_real` suffix for recomputed benchmarks:**
The dbt mart_benchmark columns (np_spend_pct_rank_specialty_2024, etc.) remain in the feature matrix as-is (even though 0-filled on DuckDB dev). The `_real` suffix columns from `compute_real_benchmarks()` are added alongside them. This allows the ML model to see both: the dbt version (0 on DuckDB, meaningful on Athena with correct specialty segmentation when HCP master is joined) and the Python version (computed from whatever spend data is available in memory). Overwriting the dbt columns would obscure which value came from which pipeline stage.

**Ground truth in separate parquet:**
Having a separate file creates a physical barrier that makes accidental contamination detectable. It also makes `test_anomaly_models.py` self-documenting — any test that reads `feature_store.parquet` is operating on features only, and any test that reads `ground_truth_labels.parquet` is doing validation.

**Event features 0-filled for non-speakers:**
95,657 HCPs have no speaker events. 0-fill is semantically correct: no events means zero speaker fees, zero attendance issues, zero FMV violations. The Isolation Forest treats a 0-vector event feature block as "no speaker program exposure," which is the right representation. Filling with mean or median would introduce false signal for inactive HCPs.

**`EXCLUDE_FROM_FEATURES` list:**
Columns are excluded for three reasons:
1. Identity (hcp_id, hcp_name, specialty, state, city) — needed for joining but not numeric signal
2. Categorical strings (engagement_quadrant, cap_pattern, spend_trend) — replaced by ordinal integer `_real` versions
3. Raw spend aliases (spend_2022_raw, etc.) — used only for recompute, already captured in scaled spend features
4. Metadata (mart_created_at) — timestamp, not signal

---

### 4. Benchmark Recompute

`compute_real_benchmarks()` resolves the known limitation documented in Tasks 2.3 and 2.4. On DuckDB dev, all spend-based benchmark columns in `mart_benchmark` are 0-filled because `mart_hcp_spend_features` (the CMS data source) is Athena-only. Once the Athena spend data is loaded into Python via `hcp_spend_features.py`, the recompute provides real values.

**7-step process:**

| Step | Input | Output columns |
|---|---|---|
| 1: Peer averages | `spend_YYYY_raw`, `specialty` | `peer_avg_2022/2023/2024` (temporary, not output columns) |
| 2: Percentile ranks | Per-specialty `rank(pct=True)` | `np_spend_pct_rank_specialty_2022/2023/2024_real` |
| 3: Spend vs peer avg | `spend / peer_avg` capped at 10.0 | `np_spend_vs_peer_avg_2022/2023/2024_real` |
| 4: Outlier flags | `rank > 0.90` | `np_spend_outlier_2022/2023/2024_real`, `np_outlier_years_count_real`, `np_persistent_outlier_real` |
| 5: Spend trend | Ordinal: decreasing=0, stable=1, net_increasing=2, increasing=3 | `spend_trend_real` |
| 6: Cap pattern | Ordinal: compliant=0, near_cap=1, chronic_near_cap=2, single_breach=3, chronic_breach=4 | `years_at_cap_real`, `years_near_cap_real`, `cap_pattern_real` |
| 7: Engagement score | `rank × 30 + outlier_count × 5` (max 45 pts without industry data) | `engagement_priority_score_real` |

**What changes after recompute vs dbt values:**
- On DuckDB dev: all `_real` columns remain 0/0.0 because `spend_2022_raw` is 0. Same behavior as dbt, but now documented explicitly.
- On Athena prod: `_real` columns carry meaningful percentile ranks and cap pattern classifications that were 0-filled in the dbt mart. The engagement_priority_score_real will correctly reflect relative spend positioning within peer groups.

---

### 5. How to Run and Verify

```bash
# Prerequisites
python3 features/hcp_spend_features.py   # requires Athena
python3 features/event_features.py        # DuckDB only

# Feature store
python3 features/feature_store.py
```

Expected outputs:
```
features/outputs/feature_store.parquet
features/outputs/ground_truth_labels.parquet
features/outputs/feature_store_metadata.json
```

Verify:
```python
import pandas as pd, json

df = pd.read_parquet('features/outputs/feature_store.parquet')
gt = pd.read_parquet('features/outputs/ground_truth_labels.parquet')

print(df.shape)                  # (97011, N)
print(df.isnull().sum().sum())   # 0
print(gt.shape)                  # (97011, 4)
print(gt.has_violation.mean())   # ~0.245

# Confirm no GT columns in feature matrix
assert 'ground_truth_violation_count' not in df.columns
assert 'has_violation' not in df.columns
```

---

### 6. Known Limitations

- **Industry benchmarks (sow_, ind_) remain 0.0** — `mart_population_payments` and `mart_competitor_payments` are Athena-only and are not loaded into the Python layer. The `np_vs_industry_ratio_*` and `sow_*` columns from mart_benchmark remain 0-filled. These are included in the feature matrix but contribute no signal on DuckDB dev. Planned for Phase 3 API layer.
- **`compute_real_benchmarks()` uses Nova Pharma spend only** — the percentile ranks are computed within the Nova Pharma HCP population, not against the CMS-wide HCP population. This is a Nova Pharma internal benchmark (TIER 1), not a true industry comparison (TIER 2).
- **Benchmark recompute uses simple percentile rank, not weighted by specialty size** — all 97,011 HCPs have `specialty = 'Unknown'` on current data (not in synthetic interactions). The recompute produces national percentile ranks until HCP master data with specialty is joined.
- **`engagement_priority_score_real` capped at 45 pts on current data** — without industry/SOW components (25+25 pts), the maximum score from available signals is 30 (NP rank) + 15 (3 outlier years × 5 pts) = 45. On Athena with real industry data loaded, the cap rises to 100.

---

### 7. Next Steps

- **Task 2.8:** `rule_based_flags.py` reads `feature_store.parquet` and applies the compliance/rules.json thresholds as binary flags for each HCP
- **Task 2.9:** Isolation Forest reads `feature_store.parquet` as its sole input
- **Task 2.12:** `test_anomaly_models.py` reads `ground_truth_labels.parquet` to measure Isolation Forest precision/recall against known violations

---

## Task 2.6 — event_features.py

### 1. Task Overview and Purpose

`event_features.py` aggregates `mart_event_features` (5,241 event-level rows) to one row per HCP speaker (1,354 rows). It is the second of two feature engineering scripts feeding into `feature_store.py` (Task 2.7).

**Why event features need separate aggregation:**
The dbt mart `mart_event_features` is intentionally one row per event — the natural grain for compliance checks like per-event meal cost or attendee count. ML models require one row per HCP. Aggregating in Python (rather than adding another dbt mart) keeps the mart grain clean and puts the aggregation logic where it can be tested and version-controlled alongside the rest of the ML pipeline.

**How 5,241 event rows become 1,354 HCP rows:**
The 5,241 events cover 1,354 distinct HCP speakers (`DISTINCT speaker_hcp_id`). Each HCP has between 1 and N events. Statistical aggregations (mean, max, sum, std, min) compress the event series into HCP-level signals. 1,165 of the 1,354 speakers have more than one event — their variability across events is captured by `std` and `event_risk_score_cv`.

**Role in feeding feature_store.py:**
This script outputs `features/outputs/event_feature_matrix.parquet` with 1,354 rows and `hcp_id` as the join key. `feature_store.py` (Task 2.7) left-joins this onto the 97,011-row HCP spend matrix — the 95,657 non-speaker HCPs receive 0-fill for all event columns.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `features/event_features.py` | Event feature engineering — load, aggregate, derive, clean, scale, save |

**8 functions:**

| Function | What it does |
|---|---|
| `load_event_features()` | Reads all columns from `mart_event_features` (DuckDB). Logs row count. Raises on failure |
| `aggregate_to_hcp_level(df)` | GROUP BY `speaker_hcp_id`. Applies `EVENT_AGG_FEATURES` (mean/max/std/sum/min). Sums `EVENT_FLAG_FEATURES` to integer counts. Adds `total_events_as_speaker`. Flattens column names |
| `compute_derived_features(df)` | Computes `pct_events_*` ratios and `event_risk_score_cv` from aggregated values. Uses `np.where` for div-by-zero safety |
| `handle_nulls(df)` | Per-column null fill: std→0.0, min→0.0, pct_→0.0, cv→0.0, catch-all→0.0 |
| `scale_features(df)` | RobustScaler on continuous cost/risk aggregations. pct_, flag counts, cv, and `total_events_as_speaker` excluded. `total_events_as_speaker` normalized to [0,1] by `/max` |
| `add_identity_columns(scaled_df)` | Resets index, renames `speaker_hcp_id` → `hcp_id`, casts to str |
| `validate_output(df)` | 5 checks: row count ≤ 1,354, no nulls, no infinities, pct_ in [0,1], total_events_as_speaker ≥ 0 |
| `save_outputs(df, scaler_params, source_rows)` | Saves parquet + metadata JSON. Returns paths dict |
| `main()` | Orchestrates all steps, logs summary |

**All output columns after aggregation:**

| Group | Columns |
|---|---|
| Risk score | `raw_event_risk_score_mean`, `raw_event_risk_score_max`, `raw_event_risk_score_std` |
| Attendance | `attendee_count_mean`, `attendee_count_min`, `attendee_count_sum` |
| Cost | `speaker_fee_mean`, `speaker_fee_max`, `speaker_fee_sum`, `total_program_cost_mean`, `total_program_cost_max`, `total_program_cost_sum`, `meal_cost_per_attendee_mean`, `meal_cost_per_attendee_max` |
| FMV | `speaker_fee_fmv_pct_mean`, `speaker_fee_fmv_pct_max` |
| Attestation | `attendees_signed_pct_mean`, `attendees_signed_pct_min` |
| Flag counts (integer) | `low_attendance_flag_sum`, `very_low_attendance_flag_sum`, `cost_per_head_over_limit_sum`, `high_venue_cost_flag_sum`, `over_total_cost_ceiling_flag_sum`, `speaker_fee_over_fmv_flag_sum`, `repeat_speaker_flag_sum`, `high_repeat_speaker_flag_sum`, `rapid_repeat_flag_sum`, `missing_attestation_flag_sum` |
| Derived | `total_events_as_speaker`, `pct_events_low_attendance`, `pct_events_over_fmv`, `pct_events_missing_attestation`, `pct_events_rapid_repeat`, `event_risk_score_cv` |
| Identity | `hcp_id` |

**Derived feature meanings:**

| Feature | Formula | Business meaning |
|---|---|---|
| `total_events_as_speaker` | `COUNT(event_id)` per HCP | Total speaker program participation — volume signal |
| `pct_events_low_attendance` | `low_attendance_flag_sum / total_events` | Fraction of events with < 3 attendees (SPEAKER_004) — pattern of nominal "programs" |
| `pct_events_over_fmv` | `speaker_fee_over_fmv_flag_sum / total_events` | Fraction of events where fee exceeded $3,500 FMV ceiling — systematic overpayment |
| `pct_events_missing_attestation` | `missing_attestation_flag_sum / total_events` | Fraction of events with < 80% signed attestations — documentation failure pattern |
| `pct_events_rapid_repeat` | `rapid_repeat_flag_sum / total_events` | Fraction of events occurring < 30 days after prior event (SPEAKER_005) |
| `event_risk_score_cv` | `raw_event_risk_score_std / raw_event_risk_score_mean` | Coefficient of variation of risk — high CV = erratic pattern; low CV + high mean = consistently risky |

---

### 3. Technical Decisions and Why

**Aggregate to HCP level in Python, not dbt:**
A `mart_event_hcp_features` dbt model would introduce another materialized table purely as an intermediate for the ML pipeline. Keeping aggregation in Python alongside the rest of the feature engineering pipeline makes the ML layer self-contained and independently testable. The dbt mart grain (one row per event) remains clean for other consumers (e.g. violation ground truth, dashboards).

**RobustScaler on cost/risk aggregations:**
Cost aggregations (`speaker_fee_max`, `total_program_cost_sum`, etc.) are the primary targets for scaling — they span several orders of magnitude (e.g. `speaker_fee_sum` ranges from a few hundred to tens of thousands). RobustScaler's median/IQR approach handles this without the extreme values collapsing the distribution that StandardScaler would cause.

**pct_ features not rescaled:**
All `pct_events_*` columns are computed as a count divided by `total_events_as_speaker`, so their natural range is [0.0, 1.0]. Rescaling would compress this range further without adding information. The Isolation Forest will treat these as already-normalized continuous inputs.

**event_risk_score_cv as sophistication signal:**
The coefficient of variation captures a compliance pattern that raw mean/max miss: erratic risk across events. An HCP whose events alternate between very high and very low risk scores may be strategically structuring programs to avoid detection — some compliant events "covering" for non-compliant ones. A uniformly high CV with a high mean is also suspicious. Both extremes are anomalous; this feature helps the Isolation Forest separate them.

**Flag columns become integer counts (not booleans):**
At the HCP level, the question is not "did this HCP ever have a low-attendance event" (binary) but "how many of their events had low attendance" (count). A speaker with 12 low-attendance events out of 12 total is different from one with 1 out of 12. The integer count carries the frequency signal that `pct_events_low_attendance` normalizes — both are included.

**`total_events_as_speaker` scaled by max (not RobustScaler):**
`total_events_as_speaker` is a count with a natural lower bound of 1. Dividing by the population maximum normalizes it to [0, 1] while preserving ordinal meaning and avoiding the median-centering of RobustScaler (which would make a speaker with the median number of events appear at 0 — no signal). The scale parameter (max value) is saved in `scaler_params` for reproducibility.

---

### 4. Aggregation Strategy

| Aggregation | Columns it applies to | Business meaning |
|---|---|---|
| `mean` | risk score, costs, attendance, FMV, attestation | Typical event for this speaker — baseline behavior |
| `max` | risk score, costs, FMV | Single worst-case exposure — most dangerous individual event |
| `sum` | speaker_fee, total_program_cost, attendee_count | Total exposure across all events — volume of activity |
| `min` | attendee_count, attendees_signed_pct | Worst-case floor — the event with fewest attendees or worst attestation |
| `std` | raw_event_risk_score | Variability of risk — is this speaker consistently risky or erratic? |
| `sum` (flags) | all `EVENT_FLAG_FEATURES` | Count of events where each compliance flag fired |

---

### 5. How to Run and Verify

```bash
python3 features/event_features.py
```

Expected output:
```
features/outputs/event_feature_matrix.parquet
features/outputs/event_feature_metadata.json
```

Verify:
```python
import pandas as pd, json

df = pd.read_parquet('features/outputs/event_feature_matrix.parquet')
print(df.shape)                  # (~1354, N)
print(df.isnull().sum().sum())   # 0

with open('features/outputs/event_feature_metadata.json') as f:
    meta = json.load(f)
print(meta['source_rows'])       # 5241
print(meta['hcp_speakers'])      # 1354
```

---

### 6. Known Limitations

- **Only 1,354 of 97,011 HCPs have speaker events.** The remaining 95,657 receive 0-fill for all event feature columns in `feature_store.py` (Task 2.7). 0-fill is appropriate — these HCPs have no speaker program exposure, so all event-derived signals should be zero.
- **std aggregations = 0 for single-event speakers.** 189 of the 1,354 speakers have exactly 1 event. Their `raw_event_risk_score_std` and `event_risk_score_cv` are 0.0 by definition — this is not a data quality issue.
- **`event_risk_score_cv` = 0 for single-event speakers.** Same root cause as above. These speakers have a point estimate of risk, not a distribution.
- **`days_since_last_event_same_speaker` not aggregated.** This column from `mart_event_features` captures the inter-event gap in days. The `rapid_repeat_flag` (< 30 days) is aggregated as a count, but the raw day counts are not included to avoid adding sparsely populated columns (NULL for all first-events-per-year).

---

### 7. Next Steps

- **Task 2.7:** `feature_store.py` merges `event_feature_matrix.parquet` with `hcp_spend_feature_matrix.parquet` on `hcp_id`. The 95,657 non-speaker HCPs receive 0-fill for all event columns. This produces the final combined feature matrix for the Isolation Forest.

---

## Task 2.5 — hcp_spend_features.py

### 1. Task Overview and Purpose

`hcp_spend_features.py` is the first layer of the Python feature engineering pipeline. It sits between the dbt mart layer and the ML model layer (Isolation Forest, Task 2.9), performing transformations that belong in Python rather than SQL:

- **Scaling** — RobustScaler needs Python/sklearn; cannot be done in SQL without hardcoding params
- **Null fill decisions** — some null semantics (e.g. yoy_growth=NULL means no prior year, not missing) require code-level documentation and per-column intent
- **Binary encoding** — boolean → int64 for sklearn compatibility
- **Metadata emission** — scaler params, feature lists, timestamps written alongside the matrix for reproducibility

**How it begins resolving the Athena/DuckDB split:**
The dbt layer keeps Athena and DuckDB as separate targets with 0-filled stubs for cross-engine data. This script reads from both simultaneously — `mart_hcp_spend_features` from Athena (CMS spend signals) and `mart_benchmark` from DuckDB (risk score ranks, peer percentiles) — and merges them on `hcp_id` into a single feature matrix. The DuckDB benchmark features are 0-filled until the feature store (Task 2.7) fully resolves the split on Athena.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `features/hcp_spend_features.py` | HCP spend feature engineering — load, merge, clean, scale, save |
| `features/outputs/.gitkeep` | Tracks the output directory in git without committing generated files |

**8 functions:**

| Function | What it does |
|---|---|
| `load_athena_spend_features()` | Connects to Athena via pyathena, reads `mart_hcp_spend_features`, derives `annual_cap_pct_used_2022/2023/2024` from per-year spend columns, returns DataFrame indexed by `hcp_id` |
| `load_duckdb_benchmark_features()` | Connects to DuckDB, reads `mart_benchmark` benchmark columns, returns DataFrame indexed by `hcp_id`. Non-fatal: logs warning and returns empty DataFrame on failure |
| `merge_features(spend_df, benchmark_df)` | Left-joins on `hcp_id` — all 97,011 Athena HCPs preserved. HCPs with no benchmark data receive 0.0 fill |
| `handle_nulls(df)` | Per-column null fill with documented semantics (see section 4) |
| `encode_binary_features(df)` | Converts bool/object boolean columns to int64 (0/1) for sklearn compatibility |
| `scale_features(df)` | RobustScaler on continuous features; percentile ranks and binary features skipped; `raw_spend_risk_score` divided by 100. Returns (scaled_df, scaler_params dict) |
| `add_identity_columns(scaled_df, original_df)` | Restores `hcp_id`, `hcp_name`, `specialty`, `state` from pre-scale DataFrame for output joining |
| `validate_output(df)` | 6 checks: row count == 97,011, no nulls, all numeric, binary 0/1 only, no infinities, `raw_spend_risk_score` in [0, 1] |
| `save_outputs(df, scaler_params)` | Saves parquet + metadata JSON to `features/outputs/`, returns paths dict |
| `main()` | Orchestrates all steps, logs summary (rows, feature count, time) |

**Output files:**

| File | Contents |
|---|---|
| `features/outputs/hcp_spend_feature_matrix.parquet` | One row per HCP · all feature columns scaled · identity columns for joining |
| `features/outputs/hcp_spend_feature_metadata.json` | `generated_at`, `source_tables`, `row_count`, `feature_columns`, `binary_columns`, `identity_columns`, `scaler`, `scaler_params`, `null_fill_strategy` |

**Feature lists:**

| Group | Columns |
|---|---|
| SPEND_FEATURES (continuous) | `spend_2022/2023/2024`, `peak_year_spend`, `annual_cap_pct_used_2022/2023/2024` (derived), `meals_over_limit_count`, `meal_breach_rate`, `max_meal_overage_pct`, `pct_food_beverage`, `pct_speaking_fee`, `pct_consulting`, `speaking_fee_total`, `speaking_fee_count`, `avg_unique_reps`, `top_rep_concentration_pct`, `yoy_growth_2223`, `yoy_growth_2324`, `raw_spend_risk_score` |
| SPEND_BINARY_FEATURES | `at_cap_flag`, `near_cap_flag`, `multi_year_increasing_flag`, `has_cms_payments`, `is_high_prescriber`, `is_kol` |
| BENCHMARK_FEATURES (from DuckDB) | `np_spend_pct_rank_specialty_2024/2023/2022`, `np_spend_vs_peer_avg_2024`, `np_outlier_years_count`, `np_persistent_outlier`, `np_escalating_rank`, `engagement_priority_score` |

---

### 3. Technical Decisions and Why

**RobustScaler over StandardScaler:**
RobustScaler uses median and IQR (interquartile range) rather than mean and standard deviation. Compliance data contains intentional outliers by design — HCPs at 2× the annual cap, speaker fees well above FMV, unusual rep concentration. StandardScaler would compress these signals toward the center; RobustScaler preserves their relative magnitude while centering on the median HCP. This is important because outliers are the anomalies the Isolation Forest needs to detect.

**Percentile ranks not rescaled:**
`np_spend_pct_rank_specialty_*` columns are already in [0.0, 1.0] from `PERCENT_RANK()` in the dbt mart. Applying RobustScaler would compress the range further (the IQR of a uniform [0,1] distribution is 0.5, so RobustScaler would roughly double the values). The existing scale directly represents the HCP's position in the peer distribution — meaningful as-is.

**Binary features not scaled:**
Boolean compliance flags (at_cap, near_cap, etc.) encode a categorical yes/no determination. Scaling them numerically would introduce a false notion of distance between 0 and 1 that carries no compliance meaning. Isolation Forest handles mixed binary/continuous inputs; binary features just need to be integer dtype.

**yoy_growth nulls filled with 0.0:**
`yoy_growth_2223` and `yoy_growth_2324` are NULL when an HCP had no spend in the prior year (`spend_2022 = 0` or `spend_2023 = 0`). NULL here means "no growth because there was no base" — not missing data. Filling with 0.0 correctly represents "no change relative to a zero baseline." Filling with mean or median would introduce false growth signals for inactive HCPs.

**Identity columns removed before scaling:**
`hcp_name`, `specialty`, `state` are strings — not compatible with sklearn transformers. `hcp_id` is the join key. All four are restored after scaling via `add_identity_columns()` so the output parquet can be joined back to other marts by `hcp_id`.

**`annual_cap_pct_used_2022/2023/2024` derived in Python:**
`mart_hcp_spend_features` stores a single `annual_cap_pct_used` column (peak-year spend / 75,000). Per-year versions are computed in `load_athena_spend_features()` as `spend_YYYY / 75000.0`. This is identical to what `mart_benchmark` computes internally. Deriving in Python avoids adding 3 columns to the SQL mart for the sole purpose of this feature script.

---

### 4. Null Handling Strategy

| Feature group | Fill value | Why |
|---|---|---|
| `yoy_growth_2223`, `yoy_growth_2324` | `0.0` | NULL = no prior year spend. Not a risk signal — represents "no growth from zero," not missing data |
| Ratio features (`top_rep_concentration_pct`, `pct_*`) | `0.0` | NULL = no activity recorded. Zero concentration/share is the correct representation |
| Binary features (`at_cap_flag`, `near_cap_flag`, etc.) | `0` (int) | NULL = flag is absent/false. Absence of a breach flag means compliant |
| Percentile ranks (`np_spend_pct_rank_specialty_*`) | `0.0` | NULL = no peer data available. Floor rank (0th percentile) is the conservative safe default |
| All other nulls | `0.0` | Safe default; logged before fill so unexpected nulls are visible in logs |

---

### 5. How to Run and Verify

```bash
python3 features/hcp_spend_features.py
```

Expected output:
```
features/outputs/hcp_spend_feature_matrix.parquet
features/outputs/hcp_spend_feature_metadata.json
```

Verify:
```python
import pandas as pd, json

df = pd.read_parquet('features/outputs/hcp_spend_feature_matrix.parquet')
print(df.shape)               # (97011, N)
print(df.isnull().sum().sum()) # 0

with open('features/outputs/hcp_spend_feature_metadata.json') as f:
    meta = json.load(f)
print(meta['row_count'])        # 97011
print(meta['scaler'])           # RobustScaler
print(len(meta['feature_columns']))  # 28
```

---

### 6. Known Limitations

- **Benchmark features 0-filled on DuckDB until Task 2.7:** `mart_benchmark` on DuckDB returns 0.0 for all spend-based ranks (`np_spend_pct_rank_specialty_*`) since CMS data is Athena-only. The benchmark columns in the matrix will be 0-filled until `feature_store.py` (Task 2.7) resolves the Athena/DuckDB split and registers all data on a single target.
- **Athena query latency:** The `mart_hcp_spend_features` query may take 30–60 seconds on a cold Athena cluster start. Subsequent runs in the same session will be faster.
- **Inverse transform not yet implemented:** `scaler_params` are saved in metadata for reproducibility and future inverse transform support, but no `inverse_transform()` function is provided in this script.
- **`is_high_prescriber` and `is_kol` are NULL everywhere:** These fields are not in the synthetic interactions data and will be NULL on both targets until HCP master data is joined. They are filled with 0 (false) via the binary null fill strategy.

---

### 7. Next Steps

- **Task 2.6:** `event_features.py` builds the event-level feature matrix from DuckDB (`mart_event_features`, `mart_speaker_events_features`, `mart_attendees_features`)
- **Task 2.7:** `feature_store.py` merges the HCP spend matrix and event matrix into a single combined feature store and fully resolves the Athena/DuckDB split
- **Task 2.9:** Isolation Forest reads from `feature_store.py` output as its primary input

---

## Task 2.4 — mart_benchmark

### 1. Task Overview and Purpose

`mart_benchmark` is a two-tier HCP peer benchmarking mart — one row per HCP. It answers four compliance questions per HCP:

1. Was each program year individually compliant vs the $75K annual cap (COMP_001)?
2. Is Nova Pharma overpaying vs our own annual engagement norms (TIER 1 NP benchmarks)?
3. Is Nova Pharma overpaying vs what the industry pays annually (TIER 2 industry benchmarks)?
4. Is Nova Pharma this HCP's dominant payer (share of wallet — OIG captured-HCP red flag)?

It produces an `engagement_quadrant` decision (investigate / review / competitive_intelligence / continue) and an `engagement_priority_score` (0-100) per HCP for 2024 as the primary signal, with 2022/2023 for pattern context.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `pipelines/dbt_project/models/marts/mart_benchmark.sql` | Two-tier benchmarking mart — 11 CTEs |

**11 CTEs:**

| CTE | Description |
|---|---|
| `hcp_base` | HCP spine from `mart_hcp_risk_profile` — identity, `combined_raw_risk_score`, `meal_breach_rate`. `specialty` coalesced to 'Unknown' (NULL everywhere in synthetic data). |
| `np_spend_base` | **Target-conditional.** Athena: joins `mart_hcp_spend_features` for `spend_2022/2023/2024`. DuckDB: 0-filled (CMS Athena-only). |
| `np_specialty_stats_yr` | GROUP BY specialty — per-year peer averages, P90, P95, P50 (median). Risk score benchmarks. |
| `np_specialty_state` | GROUP BY specialty + state — peer group size for national fallback logic (min 10 HCPs). |
| `np_percentile_ranks` | PERCENT_RANK() window functions per specialty for spend (3 years + 3yr), state, specialty×state, risk score, meal breach rate. |
| `industry_hcp_agg` | **Target-conditional.** Athena: aggregate `mart_population_payments` (13.2M rows) to HCP×year. DuckDB: empty (0-row schema-only stub). |
| `industry_stats_yr` | **Target-conditional.** Athena: GROUP BY specialty — per-year industry averages, P90, P95. DuckDB: empty stub. |
| `competitor_hcp_agg` | **Target-conditional.** Athena: aggregate `mart_competitor_payments` (4.3M rows) to HCP×year. DuckDB: empty stub. |
| `competitor_stats_yr` | **Target-conditional.** Athena: GROUP BY specialty — competitor averages. DuckDB: empty stub. |
| `pre_final` | JOIN all CTEs. Compute ratios, SOW, per-year cap flags, outlier counts. 76 columns. |
| `final` | Engagement quadrant, priority score, combined flags, metadata. SELECT * emitted. |

**Output columns by category:**

| Group | Key Columns |
|---|---|
| Identity | `hcp_id`, `specialty`, `state` |
| Per-year spend | `spend_2022`, `spend_2023`, `spend_2024`, `spend_3yr` (pattern context) |
| Annual cap compliance | `annual_cap_pct_used_2022/2023/2024`, `at_cap_*`, `near_cap_*`, `years_at_cap`, `years_near_cap`, `cap_breach_any`, `cap_pattern` |
| Spend trend | `spend_trend` (increasing/decreasing/net_increasing/stable), `spend_trend_2324`, `spend_trend_2223` |
| TIER 1 NP ranks | `np_spend_pct_rank_specialty_2022/2023/2024/3yr`, `np_spend_pct_rank_state_2024`, `np_risk_pct_rank_specialty_2024`, `np_meal_breach_pct_rank_specialty_2024` |
| TIER 1 NP stats | `np_peer_avg_spend_*`, `np_peer_p90_spend_*`, `np_peer_avg_risk_score`, `np_peer_p90_risk_score` |
| TIER 1 NP flags | `np_spend_outlier_2022/2023/2024`, `np_persistent_outlier`, `np_spend_outlier`, `np_risk_outlier`, `np_top_1pct_risk`, `np_top_5pct_risk`, `np_top_10pct_risk` |
| TIER 2 industry | `industry_spend_2022/2023/2024/3yr`, `ind_peer_avg_spend_*`, `ind_peer_p90/p95_spend_*` |
| TIER 2 NP vs industry | `np_vs_industry_ratio_2022/2023/2024/3yr`, `ind_outlier_2022/2023/2024`, `ind_high_outlier_2024`, `ind_persistent_outlier` |
| Share of wallet | `sow_2022/2023/2024/3yr`, `sow_dominant_*`, `sow_exclusive` (>80%), `sow_increasing` |
| Competitor | `competitor_spend_2022/2023/2024/3yr`, `np_vs_competitor_ratio_2024`, `comp_spend_outlier` |
| Engagement decision | `engagement_quadrant`, `engagement_quadrant_reason`, `engagement_priority_score` |
| Combined flags | `dual_outlier_flag`, `triple_signal_flag`, `escalating_risk_flag`, `chronic_risk_flag` |

---

### 3. Technical Decisions and Why

**DuckDB `LEAST()/GREATEST()` NULL behavior — critical fix:**
DuckDB returns the non-null argument when any argument is NULL (`LEAST(10.0, NULL) = 10.0`), unlike standard SQL which propagates NULL. The pattern `COALESCE(LEAST(10.0, x / NULLIF(y, 0)), 0.0)` misfires when `y` is NULL: `NULLIF(NULL, 0) = NULL`, `x / NULL = NULL`, `LEAST(10.0, NULL) = 10.0` (DuckDB), `COALESCE(10.0, 0.0) = 10.0`. This set all ratios to their cap value on DuckDB in the first iteration.

**Fix pattern:**
```sql
-- BEFORE (buggy on DuckDB):
COALESCE(LEAST(10.0, nb.spend_2024 / NULLIF(ind.ind_peer_avg_spend_2024, 0.0)), 0.0)

-- AFTER (correct on all engines):
CASE WHEN COALESCE(ind.ind_peer_avg_spend_2024, 0.0) > 0.0
     THEN LEAST(10.0, nb.spend_2024 / ind.ind_peer_avg_spend_2024)
     ELSE 0.0 END
```
Applied to all 12 ratio/SOW computations in the model.

**Annual-first design — no lifetime columns:**
OIG Fraud Alert 2020, PhRMA Code, and CMS Open Payments all operate on a per-program-year basis. Annual cap compliance is year-specific. 3-year aggregates (`spend_3yr`) are included as pattern context only and explicitly labeled as such throughout the model.

**Target-conditional CTEs for Athena-only sources:**
`mart_population_payments` and `mart_competitor_payments` are thin views over `stg_cms_general_payments` (Glue catalog, Athena-only). They do not exist in DuckDB. The 4 industry/competitor CTEs are wrapped in `{% if target.type == 'athena' %}` blocks. DuckDB versions return 0 rows using `FROM (SELECT 1 AS _dummy) _empty WHERE 1 = 0` to produce the correct schema for LEFT JOINs.

**`specialty = 'Unknown'` as national benchmark fallback:**
`specialty` is NULL for all 97,011 rows in `mart_hcp_risk_profile` (not in synthetic interactions data). COALESCE to 'Unknown' produces one national peer group until HCP master data is joined. All specialty benchmarks on current targets are effectively national benchmarks.

**Industry JOIN via CMS specialty (not mart_hcp_risk_profile specialty):**
The JOIN from `hcp_base` to `industry_stats_yr` uses `COALESCE(ihcp.specialty, h.specialty)` — preferring the specialty from `industry_hcp_agg` (sourced from `physician_specialty` in CMS Open Payments) over the hcp_base specialty (NULL everywhere). This means on Athena the industry benchmarks are segmented by the CMS-reported specialty rather than the HCP master specialty.

**`np_use_national_benchmark` flag:**
When `np_peer_group_size < 10` for a specialty×state combination, `np_use_national_benchmark = true`. The model surfaces this as a metadata flag; the actual national fallback (using specialty = 'Unknown' as the peer group) occurs naturally because `specialty` is 'Unknown' everywhere in the current data.

**Engagement quadrant logic:**
```
investigate:            2024 NP rank > 75th pct AND industry ratio > 1.5×
                        OR escalating rank (both YoY) AND chronic near-cap (≥2 years)
review:                 2024 NP rank > 75th pct AND industry ratio ≤ 1.5×
competitive_intelligence: NP rank ≤ 75th pct AND industry ratio > 1.5×
continue:               all other HCPs
```

**`engagement_priority_score` formula (0-100):**
```
LEAST(100.0,
    np_spend_pct_rank_specialty_2024 × 30     -- NP 2024 rank (30 pts)
  + LEAST(1.0, np_vs_industry_ratio_2024 / 3.0) × 25  -- industry ratio (25 pts, full at 3×)
  + sow_2024 × 25                             -- share of wallet (25 pts)
  + np_outlier_years_count × 5.0             -- NP persistence (10 pts max)
  + ind_outlier_years_count × 5.0            -- industry persistence (10 pts max)
)
```
On DuckDB, spend = 0 for all HCPs → rank = 0, industry ratio = 0, SOW = 0 → score = 0 by design.

---

### 4. Verification Results (DuckDB dev)

| Check | Expected | Actual |
|---|---|---|
| Row count | 97,011 | 97,011 ✓ |
| `np_vs_industry_ratio_2024` | 0.0 (no CMS on DuckDB) | 0.0 ✓ |
| `sow_2024` | 0.0 (no CMS on DuckDB) | 0.0 ✓ |
| `engagement_quadrant` | 'continue' for all (no spend data) | 'continue' (97,011) ✓ |
| `np_top_1pct_risk` | ~1% of 97,011 ≈ 970 | 962 ✓ |
| `cap_pattern` | 'compliant' for all (spend = 0) | 'compliant' (97,011) ✓ |
| dbt tests | 11/11 PASS | 11/11 PASS ✓ |

---

### 5. Known Limitations

- **DuckDB: all spend = 0** — `mart_hcp_spend_features` depends on `mart_target_payments` (Athena-only CMS source). All per-year spend columns, spend ranks, cap compliance, SOW, and industry ratios are 0-filled on DuckDB. Only risk score ranks and meal breach ranks are meaningful on DuckDB.
- **DuckDB: engagement_quadrant = 'continue' for all** — correct; meaningful quadrant assignments require Athena CMS data.
- **specialty = 'Unknown' everywhere** — synthetic interactions data does not include HCP specialty. All specialty benchmarks are national benchmarks on current targets. Will segment correctly when HCP master data is joined.
- **Industry JOIN uses CMS specialty** — on Athena, industry benchmarks join via `physician_specialty` from CMS Open Payments, not from an HCP master table. Specialty mismatches between CMS records and HCP master data will affect benchmark accuracy.
- **3-year aggregates include overlapping HCPs** — HCPs who received payments in only 1 or 2 of the 3 years will have lower 3yr aggregates than active HCPs. This is by design (reflecting actual engagement) but means `spend_3yr` is not normalized for tenure.

---

## Task 2.3 — mart_hcp_risk_profile

### 1. Task Overview and Purpose

`mart_hcp_risk_profile` is the master HCP risk spine — one row per HCP joining all Phase 2 feature marts into a single ML-ready table. It is the primary input for the Isolation Forest (Task 2.9), rule-based flags (Task 2.8), and the unified scorer (Task 2.10).

Three independent data sources each capture a different angle of HCP risk:
- **CMS Open Payments** (`mart_hcp_spend_features`) — what Nova Pharma reported to the government; external, independently verifiable
- **Speaker events** (`mart_event_features`) — OIG Fraud Alert primary signals; internal program-level compliance data
- **CRM interactions** (`mart_hcp_interactions_features`) — meal frequency, rep visits, FMV compliance, documentation quality; internal CRM

No single source is complete. CMS data lives only in Athena; synthetic event and interaction data live only in DuckDB. This mart handles cross-engine incompleteness via target-conditional 0-filled CTEs and a null-safe combined score that reweights proportionally to available sources.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `pipelines/dbt_project/models/marts/mart_hcp_risk_profile.sql` | Master risk spine SQL — 6 target-conditional CTEs |

**6 CTEs:**

| CTE | Athena | DuckDB |
|---|---|---|
| `hcp_spine` | From `mart_hcp_spend_features` (97,011 CMS-known HCPs) | Aggregated from `mart_hcp_interactions_features` GROUP BY hcp_id |
| `spend_features` | From `mart_hcp_spend_features` | 0-filled (27 columns) |
| `event_agg` | 0-filled | Aggregated from `mart_event_features` GROUP BY speaker_hcp_id |
| `interaction_features` | 0-filled | Aggregated from `mart_hcp_interactions_features` GROUP BY hcp_id |
| `ground_truth_agg` | 0-filled | Aggregated from `mart_violation_ground_truth` GROUP BY hcp_id |
| `final` | All LEFT JOINs, COALESCE, combined score, GT with VALIDATION ONLY comment | Same |

**Output columns by category:**

| Group | Key Columns |
|---|---|
| Identity | `hcp_id`, `hcp_name` (NULL — not in synthetic data), `city`, `state`, `specialty`, `is_kol`, `is_high_prescriber` |
| CMS spend | `lifetime_total_spend`, `peak_year_spend`, `annual_cap_pct_used`, `at_cap_flag`, `near_cap_flag`, `meal_breach_rate`, `max_meal_overage_pct`, `pct_speaking_fee`, `multi_year_increasing_flag`, `raw_spend_risk_score` |
| Speaker events | `total_events_as_speaker`, `avg_event_risk_score`, `max_event_risk_score`, `events_with_low_attendance`, `events_over_fmv`, `events_missing_attestation`, `events_rapid_repeat`, `total_speaker_fees_events`, `pct_events_over_fmv` |
| Interactions | `total_interactions`, `total_meals`, `avg_meal_cost`, `interactions_with_vague_rationale`, `fmv_compliance_rate`, `unique_reps_interacted`, `interaction_frequency_score` |
| Completeness | `has_cms_payments`, `has_speaker_events`, `has_interactions`, `data_completeness_score` (0-3), `risk_signal_count` |
| Combined score | `combined_raw_risk_score` (0-100 heuristic, pre-ML) |
| Ground truth | `ground_truth_violation_count`, `ground_truth_max_severity` — VALIDATION ONLY |

---

### 3. Technical Decisions and Why

**HCP spine on DuckDB uses `mart_hcp_interactions_features`, not `stg_synthetic_interactions`:**
`stg_synthetic_interactions` contains one row per interaction (995K rows). Selecting `DISTINCT hcp_id` would work but requires a full scan of the raw staging table. Using the already-materialized `mart_hcp_interactions_features` (also interaction-level, but with cleaner fields) is more consistent with the rest of the mart's dependency chain and avoids redundant staging table reads.

**`interaction_features` CTE aggregates an interaction-level mart:**
`mart_hcp_interactions_features` is one row per interaction, not per HCP. The `interaction_features` CTE contains a full GROUP BY aggregation to produce one HCP-level row. The CTE name was chosen to match the logical concept (HCP-level interaction features) rather than the physical source (interaction-level mart).

**Vague rationale defined as `IN ('', 'Meeting', 'Other') OR IS NULL`:**
Actual data exploration on the synthetic interactions found these three string values collectively account for ~53K low-quality documentation entries. Single-word or empty rationales indicate documentation controls are not being followed — a behavioral signal separate from FMV compliance.

**`interaction_frequency_score` weights (50/30/20):**
- 50 pts for volume (100 interactions → 50 pts): captures high-frequency relationships regardless of compliance status
- 30 pts for FMV violations (0% compliance → 30 pts): FMV excess is the primary quid-pro-quo mechanism
- 20 pts for documentation quality (100% vague → 20 pts): documentation weakness is a lagging indicator of behavioral risk

**Null-safe combined score with 7 CASE branches:**
The 7 branches cover all combinations of available sources (2³ = 8 states minus the "all absent" → 0.0 fallback). Each branch uses proportional reweighting — e.g., events+interactions only uses weights `0.35/0.60 ≈ 0.583` and `0.25/0.60 ≈ 0.417` so the output still sums to 100% of the original scale. This prevents the combined score from being systematically lower for HCPs with fewer data sources.

**`CURRENT_TIMESTAMP` instead of `CAST(NOW() AS TIMESTAMP)` for `mart_created_at`:**
This model is DuckDB-primary (`--target dev`). DuckDB supports `CURRENT_TIMESTAMP` natively. On Athena, `CURRENT_TIMESTAMP` returns `timestamp(3) with time zone` which fails Hive table storage — but the Athena target does not use this model's full form (event/interaction/GT CTEs are 0-filled on Athena, and the Athena primary model for spend is `mart_hcp_spend_features`).

---

### 4. Business Rules Applied (all sourced from compliance/rules.json)

| Rule ID | Rule | Threshold | Applied As |
|---|---|---|---|
| COMP_001 | Annual cap | $75,000 | `at_cap_flag`, `near_cap_flag`, `annual_cap_pct_used` (via spend features) |
| COMP_003 | Near-cap threshold | 80% ($60K) | `near_cap_flag` |
| MEAL_003 | Dinner ceiling | $100 | `meal_breach_rate`, `max_meal_overage_pct` (via spend features) |
| SPEAKER_001 | Speaker FMV ceiling | $3,500 | `events_over_fmv`, `pct_events_over_fmv` (via event agg) |
| SPEAKER_004 | Min attendees | 3 | `events_with_low_attendance` (via event agg) |
| ATTEST_001 | Min attestation rate | 80% | `events_missing_attestation` (via event agg) |
| SPEAKER_005 | Rapid repeat window | 30 days | `events_rapid_repeat` (via event agg) |

---

### 5. How the Combined Score Works

`combined_raw_risk_score` is a null-safe weighted average of the three source risk scores, computed only from available data sources.

**Full weights (all three sources):**
```
spend * 0.40 + avg_event_risk_score * 0.35 + interaction_frequency_score * 0.25
```

**DuckDB (no CMS spend — events + interactions only):**
```
avg_event_risk_score * (0.35/0.60) + interaction_frequency_score * (0.25/0.60)
≈ avg_event_risk_score * 0.583 + interaction_frequency_score * 0.417
```

**Athena (no events/interactions — spend only):**
```
raw_spend_risk_score * 1.0
```

---

### 6. How to Run and Verify

**Run:**
```bash
cd pipelines/dbt_project
dbt run --select mart_hcp_risk_profile
dbt test --select mart_hcp_risk_profile
```

**Expected:**
```
1 of 1 OK created sql table model main.mart_hcp_risk_profile [OK]
9/9 tests PASS
```

**Spot-check queries:**
```python
import duckdb
con = duckdb.connect('data/processed/compliance.duckdb')

con.execute('SELECT COUNT(*) FROM mart_hcp_risk_profile').fetchone()
# (97011,)

con.execute('SELECT COUNT(*) FROM mart_hcp_risk_profile WHERE has_speaker_events = true').fetchone()
# (1354,)  — 1.4% of HCPs were speakers (realistic for a pharma program)

con.execute('SELECT ROUND(AVG(combined_raw_risk_score), 3) FROM mart_hcp_risk_profile').fetchone()
# (7.576,)  — DuckDB combined = events*0.583 + interactions*0.417; most HCPs have low event scores

con.execute('SELECT COUNT(*) FROM mart_hcp_risk_profile WHERE ground_truth_violation_count > 0').fetchone()
# (23727,)  — 24.5% of HCPs have at least one flagged violation in ground truth

# Completeness distribution
con.execute('''
    SELECT data_completeness_score, COUNT(*)
    FROM mart_hcp_risk_profile GROUP BY 1 ORDER BY 1
''').fetchall()
# [(1, 95657), (2, 1354)]
# — On DuckDB: all HCPs have interactions; 1,354 also have speaker events; no CMS spend (0-filled)

# GT severity distribution
con.execute('''
    SELECT ground_truth_max_severity, COUNT(*)
    FROM mart_hcp_risk_profile GROUP BY 1 ORDER BY 1
''').fetchall()
# [('high', 1034), ('low', 3335), ('medium', 19358), ('none', 73284)]
```

---

### 7. Known Limitations

1. **DuckDB has no CMS spend** — `has_cms_payments` is always `false` on the dev target; `raw_spend_risk_score` is always 0. The combined score on DuckDB reflects only events + interactions. Full 3-source scoring requires synthetic data registered in Glue (future).

2. **`data_completeness_score` max is 2 on DuckDB** — because CMS spend is always absent on the dev target. A score of 3 is only achievable on Athena after synthetic data is registered in Glue.

3. **`hcp_name`, `specialty`, `is_kol`, `is_high_prescriber` are NULL** — these fields are not present in the synthetic data sources. They are placeholders for future HCP master data enrichment.

4. **Ground truth is DuckDB-only** — `ground_truth_violation_count` and `ground_truth_max_severity` are 0-filled on Athena because `mart_violation_ground_truth` is built from synthetic data that doesn't exist in Glue. This is by design — these fields are VALIDATION ONLY and should not be present in production Athena runs.

5. **`avg_event_risk_score` vs `max_event_risk_score` in combined score** — the combined score uses `avg_event_risk_score` (mean across all events a speaker gave). This may underweight HCPs who had one very high-risk event alongside many compliant events. Task 2.10 (`scorer.py`) will address this with a more sophisticated weighting.

---

### 8. Next Steps

- **Task 2.4** (`mart_benchmark.sql`): Adds peer percentile ranks for key risk signals (e.g., `pct_speaking_fee_percentile`) as additional features
- **Task 2.8** (`rule_based_flags.py`): Reads this mart and applies `get_rule()` thresholds to produce deterministic violation flags for the ensemble
- **Task 2.9** (`isolation_forest.py`): Uses this mart's numeric columns as the Isolation Forest feature matrix
- **Task 2.10** (`scorer.py`): Replaces `combined_raw_risk_score` with a unified ML-informed score that combines IF anomaly scores with rule-based flags

---

## Task 2.2 — mart_event_features

### 1. Task Overview and Purpose

`mart_event_features` produces one ML-ready row per speaker program event, aggregating cost, attendance, attestation, and speaker-repeat signals for anomaly detection. It is the event-level counterpart to `mart_hcp_spend_features` (which is HCP-level).

Speaker programs are a priority OIG enforcement area. The 2020 OIG Special Fraud Alert on speaker programs explicitly identifies: low attendance at "educational" events, lavish venues, high venue costs, and the same speaker repeatedly presenting to the same audience as hallmarks of payments made under the guise of education. This mart operationalises those red flags as numeric features.

**How it fits into the Phase 2 pipeline:**
- Feeds `mart_hcp_risk_profile` (Task 2.3) where event features are aggregated to the HCP level and joined with external CMS spend signals
- Feeds `event_features.py` (Task 2.6) for Python-side feature engineering
- Feeds the EDA notebook (Task 2.13) for risk score distribution analysis

All business rule thresholds are sourced from `compliance/rules.json` (Task 2.0b output) — no magic numbers in the SQL.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `pipelines/dbt_project/models/marts/mart_event_features.sql` | Main mart SQL — 5 CTEs, 30+ output columns |

**5 CTEs:**

| CTE | Purpose |
|---|---|
| `events_base` | Cleans identity, cost, and compliance fields from `stg_synthetic_speaker_programs`; COALESCEs cost columns to 0; casts types for window compatibility |
| `attendee_agg` | Aggregates `stg_synthetic_attendees` per event: total attendee count, signed attestation count, and `attendees_signed_pct` (with NULLIF divide-by-zero guard) |
| `speaker_window` | Window functions partitioned by `(speaker_hcp_id, event_year)` ordered by `event_date ASC`: `events_same_speaker_year` (total count per year), `days_since_last_event_same_speaker` (LAG date diff) |
| `cost_features` | Joins `events_base` with `attendee_agg`; derives `meal_cost_per_attendee`, `venue_cost_pct_of_total`, `speaker_fee_fmv_pct`; applies `NULLIF` guards |
| `final` | Joins all CTEs; applies COALESCE nulls to 0; computes all flags and `raw_event_risk_score`; adds `mart_created_at` |

**Output columns by category:**

| Group | Columns |
|---|---|
| Identity | `event_id`, `event_date`, `event_year`, `speaker_hcp_id`, `venue_city`, `venue_state`, `product_featured`, `compliance_approved` |
| Attendance | `attendee_count`, `low_attendance_flag`, `very_low_attendance_flag` |
| Cost | `speaker_fee`, `venue_cost`, `travel_reimbursement`, `total_program_cost`, `meal_cost_per_attendee`, `cost_per_head_over_limit`, `venue_cost_pct_of_total`, `high_venue_cost_flag`, `over_total_cost_ceiling_flag` |
| Speaker FMV | `speaker_fee_fmv_pct`, `speaker_fee_over_fmv_flag` |
| Repeat patterns | `events_same_speaker_year`, `repeat_speaker_flag`, `high_repeat_speaker_flag`, `days_since_last_event_same_speaker`, `rapid_repeat_flag` |
| Attestation | `attendees_signed_pct`, `missing_attestation_flag` |
| Composite | `raw_event_risk_score`, `mart_created_at` |

**Window functions:**
- `COUNT(*) OVER (PARTITION BY speaker_hcp_id, event_year)` — total events this speaker gave in this year; used for `events_same_speaker_year`, `repeat_speaker_flag`, `high_repeat_speaker_flag`
- `LAG(event_date) OVER (PARTITION BY speaker_hcp_id, event_year ORDER BY event_date ASC)` — previous event date for same speaker in same year; subtracted from current to produce `days_since_last_event_same_speaker`

---

### 3. Technical Decisions and Why

**$100 per-head ceiling (MEAL_003 from rules.json):**
The Nova Pharma internal policy (DOC_002) extracted a stricter dinner ceiling of $100 vs the PhRMA standard of $125. The `stricter-wins` reconciliation in Task 2.0b selected $100. Since CMS Open Payments and the synthetic event data don't include meal type (breakfast/lunch/dinner), the dinner ceiling is used as the single per-head limit — the most common meal format and the most defensible uniform threshold.

**$3,500 speaker FMV ceiling (SPEAKER_001 from rules.json):**
Nova Pharma's synthetic policy extracted $3,500 as the per-event speaker fee FMV ceiling, stricter than the $4,000 fallback. `speaker_fee_fmv_pct = speaker_fee / 3500.0` scales the fee as a fraction of the FMV ceiling for the risk score component.

**80% attestation threshold (ATTEST_001 from rules.json):**
The 80% threshold was not found in any extracted policy chunk (returned as fallback). It represents a commonly used internal compliance standard: events where fewer than 80% of attendees sign attestation forms suggest inadequate documentation controls.

**LAG window ordered by `event_date ASC`:**
`ASC` order ensures the LAG function references the chronologically prior event. `DESC` would reference the next event, producing nonsensical negative day-difference values. The partition resets per year, so the first event of each year correctly gets a NULL lag (no prior event in that year).

**Cost ratios capped at 1.0 before risk score multiplication:**
`LEAST(1.0, meal_cost_per_attendee / 100.0)` prevents a single extreme value from consuming the entire score component. Without the cap, a $2,000 per-head cost would produce `20 * 25 = 500` — a score component 20× its maximum allocation. Capping ensures the risk score stays within [0, 100].

---

### 4. Business Rules Applied (all sourced from compliance/rules.json)

| Rule ID | Rule | Threshold | Applied As |
|---|---|---|---|
| MEAL_003 | Per-head meal ceiling | $100 | `cost_per_head_over_limit`, meal component of risk score |
| SPEAKER_001 | Speaker FMV ceiling | $3,500 | `speaker_fee_over_fmv_flag`, FMV component of risk score |
| SPEAKER_002 | High repeat threshold | > 6 events/year | `high_repeat_speaker_flag` |
| SPEAKER_003 | Repeat threshold | > 3 events/year | `repeat_speaker_flag` |
| SPEAKER_004 | Min attendees | 3 | `low_attendance_flag` (< 3), `very_low_attendance_flag` (< 2) |
| SPEAKER_005 | Rapid repeat window | 30 days | `rapid_repeat_flag` |
| VENUE_001 | Max venue cost | $3,000 | `high_venue_cost_flag`, venue component of risk score |
| VENUE_002 | Max total program cost | $8,000 | `over_total_cost_ceiling_flag` |
| ATTEST_001 | Min attestation rate | 80% | `missing_attestation_flag`, attestation component of risk score |

---

### 5. How the Risk Score Works

`raw_event_risk_score` is a 0-100 heuristic score (pre-ML) reflecting the OIG Fraud Alert's primary red flags for speaker program abuse.

| Component | Formula | Max Points | Rationale |
|---|---|---|---|
| Attestation gap | `(1 - attendees_signed_pct) * 25` | 25 | Missing signatures = compliance failure; highest weight because it is directly actionable |
| Meal cost overage | `LEAST(1, meal_cost_per_head / 100) * 25` | 25 | Per-head cost vs MEAL_003 ceiling; tied for highest weight because it is the most common OIG flag |
| Venue cost | `LEAST(1, venue_cost / 3000) * 20` | 20 | Lavish venues are a core OIG Fraud Alert red flag |
| Speaker FMV | `LEAST(1, speaker_fee / 3500) * 20` | 20 | Above-FMV compensation is the primary quid-pro-quo mechanism |
| Low attendance | Binary 10 pts when < 3 attendees | 10 | Low attendance signals nominal educational justification |
| **Total** | | **100** | |

**Observed distribution (2026-04-02):** avg=56.76, max=100.0

The high average (56.76 vs 0.67 in `mart_hcp_spend_features`) reflects that the attestation gap component is contributing broadly — most synthetic events have some unsigned attendees. This is expected from the synthetic data generator's statistical distributions.

---

### 6. How to Run and Verify

**Run:**
```bash
cd pipelines/dbt_project
dbt run --select mart_event_features
dbt test --select mart_event_features
```

**Expected:**
```
1 of 1 OK created sql table model main.mart_event_features [OK 5241]
6/6 tests PASS
```

**Verify row count and key signal prevalence:**
```python
import duckdb
con = duckdb.connect('data/processed/compliance.duckdb')
con.execute("""
SELECT
  COUNT(*)                                                    AS total_events,
  SUM(CASE WHEN low_attendance_flag        THEN 1 ELSE 0 END) AS low_attendance,
  SUM(CASE WHEN cost_per_head_over_limit   THEN 1 ELSE 0 END) AS cost_over_limit,
  SUM(CASE WHEN speaker_fee_over_fmv_flag  THEN 1 ELSE 0 END) AS fmv_exceeded,
  SUM(CASE WHEN repeat_speaker_flag        THEN 1 ELSE 0 END) AS repeat_speaker,
  SUM(CASE WHEN missing_attestation_flag   THEN 1 ELSE 0 END) AS missing_attest,
  ROUND(AVG(raw_event_risk_score), 2)                         AS avg_risk
FROM main.mart_event_features
""").df()
```

**Observed results (2026-04-02):**
```
total_events:  5,241
low_attendance: 699  (13.3%)
cost_over_limit: 5,194  (99.1% — synthetic data broad distribution)
fmv_exceeded: 1,688  (32.2%)
repeat_speaker: 1,137 (21.7%)
high_repeat: 51  (1.0%)
missing_attest: 1,027 (19.6%)
rapid_repeat: 616 (11.8%)
avg_risk_score: 56.76
max_risk_score: 100.0
```

---

### 7. Known Limitations

1. **Synthetic data only** — no real CMS speaker event data. The synthetic generator's distributions may not match real-world prevalence rates (e.g., the 99.1% cost-over-limit rate is an artifact of synthetic `meal_cost_per_attendee` distributions, not a finding).

2. **Meal type not tracked** — the $100 dinner ceiling is applied uniformly to all per-head cost calculations. Events with breakfast or lunch spend would have a lower threshold ($25 or $50 per MEAL_001/MEAL_002), but meal type is not available in the synthetic event data.

3. **`days_since_last_event_same_speaker` is NULL for first event per speaker per year** — this is expected behavior, not missing data. The window function has no prior row to LAG from. `rapid_repeat_flag` correctly evaluates to `false` for NULL lag values.

4. **Rep identity not tracked per event** — the speaker program data includes `speaker_hcp_id` but not a rep or territory identifier. Rep-level network concentration features (planned for Phase 3) are not available in this mart.

---

### 8. Next Steps

- **Task 2.3** (`mart_hcp_risk_profile`): Aggregates this mart to HCP level (avg score, flag counts per speaker) and joins with `mart_hcp_spend_features` into the master HCP risk spine
- **Task 2.6** (`event_features.py`): Reads this mart and engineers additional Python-side features (e.g., topic diversity index, geographic concentration)
- **Task 2.13** (EDA notebook): Visualises risk score distribution, attendance patterns, and flag co-occurrence

---

## Task 2.0b — business_rules_registry.py

### 1. Task Overview and Purpose

`business_rules_registry.py` RAGs against the Qdrant `policy_docs` collection to extract compliance rule thresholds from 5 embedded policy documents, reconciles conflicting values across authorities, and writes a versioned `compliance/rules.json` registry.

`rules.json` is the **single source of truth** for all business rule constants used throughout Phase 2. Rather than each dbt model or Python script hard-coding `$125` or `$75K`, they call `get_rule("MEAL_003")["effective_threshold"]` — which always returns the authoritative reconciled value. If the policy docs are re-embedded with updated PDFs and the registry is re-generated, every downstream component automatically picks up the new thresholds.

**Why RAG-based extraction instead of hard-coding:**
Hard-coding assumes the person writing the code has correctly read and interpreted every policy document. RAG-based extraction grounds thresholds in the actual document text — GPT-4o finds the explicit numeric values and returns null when a threshold isn't stated, rather than guessing. The reconciliation step then applies a defined authority hierarchy to resolve conflicts. The process is auditable: every rule in `rules.json` records which document chunk it came from.

**How `rules.json` is used downstream:**

```python
from pipelines.business_rules_registry import get_rule

meal_limit    = get_rule("MEAL_003")["effective_threshold"]   # 100 (Nova Pharma)
annual_cap    = get_rule("COMP_001")["effective_threshold"]   # 75000
attest_min    = get_rule("ATTEST_001")["effective_threshold"] # 0.8 (fallback)
```

**The `get_rule()` utility function pattern:**
`get_rule(rule_id)` is designed to be imported and called at module load time in rule_based_flags.py, scorer.py, and feature scripts. It handles the file-not-found case gracefully (falls back to `FALLBACK_RULES` dict) and fills null effective thresholds from the `fallback_rules` section of `rules.json`. Downstream code never needs to deal with None thresholds.

---

### 2. What Was Built

| File | Purpose |
|---|---|
| `pipelines/business_rules_registry.py` | Main pipeline script |
| `compliance/rules.json` | Generated rules registry (committed to git) |

**5 Functions:**

| Function | Purpose |
|---|---|
| `query_qdrant(query_text, top_k, filter_authority)` | Embeds query via ada-002, searches Qdrant with optional authority filter, returns chunk list with scores |
| `extract_rules_from_chunks(chunks, rule_category, rule_definitions)` | Builds GPT-4o prompt with chunk context and rule list, parses JSON response, returns `{rule_id: value}` |
| `reconcile_rules(extractions_by_authority, rule_id, rule_def, chunks_by_authority)` | Applies authority hierarchy and stricter-wins logic, builds complete rule dict with source metadata |
| `build_rules_registry()` | Orchestrates all 7 categories, returns `(registry_dict, stats)` |
| `save_rules_registry(registry)` | Creates `compliance/` directory, writes `rules.json`, returns path |
| `get_rule(rule_id, rules_json_path)` | Utility for downstream import — loads `rules.json`, returns rule dict with guaranteed non-null threshold |

**7 Rule categories and 24 rule IDs:**

| Category | Rule IDs |
|---|---|
| `meal_limits` | MEAL_001, MEAL_002, MEAL_003, MEAL_004 |
| `speaker_programs` | SPEAKER_001, SPEAKER_002, SPEAKER_003, SPEAKER_004, SPEAKER_005 |
| `venue_event_costs` | VENUE_001, VENUE_002, VENUE_003 |
| `hcp_compensation` | COMP_001, COMP_002, COMP_003 |
| `interaction_frequency` | FREQ_001, FREQ_002, FREQ_003 |
| `attestation_documentation` | ATTEST_001, ATTEST_002, ATTEST_003 |
| `prohibited_practices` | PROHIBIT_001, PROHIBIT_002, PROHIBIT_003 |

**`rules.json` schema:**
```json
{
  "metadata": {
    "version", "generated_at", "generated_by",
    "qdrant_collection", "total_chunks_queried",
    "total_rules_extracted", "documents_used"
  },
  "rules": [
    {
      "rule_id", "rule_name", "category",
      "threshold", "unit", "threshold_type",
      "sources": [{"doc_id", "authority", "doc_type", "chunk_id", "extracted_value"}],
      "nova_pharma_value", "industry_value",
      "effective_threshold", "effective_source",
      "single_source", "reconciliation_note",
      "violation_type", "severity", "applies_to",
      "used_fallback", "extracted_at"
    }
  ],
  "fallback_rules": { "MEAL_001": 30.0, ... }
}
```

---

### 3. Technical Decisions and Why

**GPT-4o for extraction (not ada-002):**
ada-002 is an embedding model — it has no generation capability. Extraction requires reading text and producing a structured JSON response. GPT-4o is used for its strong instruction-following and JSON output reliability. ada-002 is still used for the Qdrant similarity search step.

**`temperature=0.0` for deterministic extraction:**
Rule threshold extraction must be reproducible. A temperature of 0 ensures GPT-4o returns the same JSON given the same input — essential for a versioned registry that is committed to git and used as a reference.

**`response_format={"type": "json_object"}`:**
Forces GPT-4o to return valid JSON unconditionally, eliminating the need for regex stripping of markdown code fences. If the model would otherwise wrap its response in ` ```json ` blocks, this parameter prevents it.

**`fallback_rules` section in `rules.json`:**
Extraction returns null when a threshold is not explicitly stated in the retrieved chunks. Fallback values ensure every rule has a non-null effective threshold regardless of extraction success. The fallback values match the constants used in `mart_hcp_spend_features.sql`, so the system is self-consistent even when RAG extraction fails.

**Authority hierarchy: OIG > Nova Pharma > PhRMA > CMS:**
- OIG (Office of Inspector General) issues regulatory enforcement guidance — these represent the government's position on what constitutes fraud and abuse
- Nova Pharma internal policy may be stricter than OIG/PhRMA (companies often self-impose tighter limits for risk management); it ranks second because stricter-wins logic still applies
- PhRMA Code is industry self-regulation — influential but not legally binding
- CMS data dictionary is reference material, not a rules document; it ranks last

---

### 4. Reconciliation Logic

**Stricter-wins approach:**
For `threshold_type = "maximum"` (meal limits, caps): the lower value is stricter.
For `threshold_type = "minimum"` (attestation rate, min attendees): the higher value is stricter.
For `threshold_type = "prohibited"` or `"required"`: `True` is always the stricter value.

The reconciliation considers all non-null extracted values across authorities, applies the hierarchy to pick the first (highest-authority) value, then checks whether any lower-authority value is stricter. If a stricter value exists at a lower-authority source, it wins regardless.

**Single-source rules:**
When only one authority's chunks contain an explicit threshold, `single_source = true` is flagged in the rule record. This signals that the value hasn't been cross-validated against another document — downstream code can use this to apply additional caution flags.

**Actual extraction results (2026-04-02):**

| Rule | Effective | Source | Note |
|---|---|---|---|
| MEAL_001–003 | 25/50/100 | Nova Pharma | Synthetic policy; stricter than standard $30/$75/$125 |
| SPEAKER_001 | 3500 | Nova Pharma | Stricter than typical $4,000 FMV ceiling |
| SPEAKER_003 | 6 | Nova Pharma | Repeat speaker threshold |
| SPEAKER_004 | 3 | Nova Pharma | Min attendees |
| VENUE_003 | 100 | Nova Pharma | Stricter per-head meal ceiling |
| COMP_001 | 75000 | Nova Pharma | Annual cap confirmed |
| ATTEST_002/003 | True | PhRMA | Documentation required |
| PROHIBIT_001–003 | True | PhRMA | Confirmed in both PhRMA and OIG chunks |
| 11 rules | fallback | — | Not explicitly stated in retrieved chunks |

Note: OIG precedence = 0 because OIG chunks for meal limits and compensation didn't contain explicit numeric thresholds — OIG guidance is qualitative ("reasonable", "not substantial") rather than specifying dollar amounts.

---

### 5. How to Run and Verify

**Run:**
```bash
python pipelines/business_rules_registry.py
```

**Prerequisites:** Qdrant must be running with 128 points in `policy_docs`.

**Expected output:**
```
Total rules:          24
Chunks queried:       126
Documents used:       DOC_001, DOC_002, DOC_003, DOC_004, DOC_005
OIG precedence:       0 rules
Nova Pharma override: 8 rules
Fallback used:        11 rules
```

**Verify JSON validity:**
```bash
cat compliance/rules.json | python3 -m json.tool
```

**Check all 24 rule IDs present:**
```bash
python3 -c "
import json
with open('compliance/rules.json') as f:
    r = json.load(f)
ids = [rule['rule_id'] for rule in r['rules']]
print(f'Rules: {len(ids)}')
print(ids)
nulls = [rule['rule_id'] for rule in r['rules'] if rule['effective_threshold'] is None]
print(f'Null thresholds: {nulls}')
"
```

**Use `get_rule()` in a downstream script:**
```python
from pipelines.business_rules_registry import get_rule

print(get_rule("MEAL_003")["effective_threshold"])   # 100
print(get_rule("COMP_001")["effective_threshold"])   # 75000
print(get_rule("PROHIBIT_001")["effective_threshold"])  # True
```

---

### 6. Known Limitations

1. **Synthetic Nova Pharma policy** — `nova_pharma_internal_policy_SYNTHETIC.pdf` was generated by `policy_doc_loader.py`. Its thresholds (e.g., MEAL_003=100 vs standard $125) are stricter than the PhRMA Code defaults. In production, real internal policy thresholds should be used.

2. **OIG guidance is qualitative** — OIG's CPG and fraud alert documents use language like "reasonable", "not substantial", and "consistent with fair market value" rather than specifying numeric dollar amounts. This is why OIG precedence = 0: GPT-4o correctly returned null rather than inferring a number. The stricter-wins logic still works — it just means OIG didn't provide competing numeric thresholds.

3. **`rules.json` is static after generation** — if the policy PDFs are updated and re-embedded, the registry must be re-run manually (`python pipelines/business_rules_registry.py`). There is no auto-refresh mechanism.

4. **PhRMA Code meal limits not extracted** — the PhRMA Code chunks retrieved for the meal_limits query did not contain the explicit $30/$75/$125 thresholds (those paragraphs may be in chunks not retrieved by the top-5 search). The synthetic Nova Pharma policy's stricter values were found instead. Future improvement: increase top_k or use targeted page-range queries.

5. **No deduplication across re-runs** — each run overwrites `compliance/rules.json` entirely. Git history provides the audit trail.

---

### 7. Next Steps

- **Task 2.2** (`mart_event_features.sql`): Uses thresholds from `rules.json` via `get_rule()` rather than hard-coded constants
- **Task 2.8** (`rule_based_flags.py`): Imports `get_rule()` for every threshold comparison — no magic numbers
- **Task 2.10** (`scorer.py`): Uses `severity` field from rules to weight the composite anomaly score
- **Phase 3 Policy Agent**: Queries the same `policy_docs` Qdrant collection for natural language policy Q&A using LangChain retrieval chain

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
