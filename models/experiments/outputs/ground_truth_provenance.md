# Ground Truth Label Provenance

**Question:** Are `ground_truth_max_severity` and `has_violation` derived from the
rule engine output (making rules-only evaluation circular), or from independent
synthetic HCP ground truth (making rules-only recall a genuine finding)?

**Files examined:**
- `pipelines/ingest/synthetic_generator.py` — data generation + violation detection
- `pipelines/dbt_project/models/staging/stg_synthetic_interactions.sql` — pass-through
- `pipelines/dbt_project/models/staging/stg_synthetic_speaker_programs.sql` — pass-through
- `pipelines/dbt_project/models/marts/mart_violation_ground_truth.sql` — record union
- `pipelines/dbt_project/models/marts/mart_hcp_risk_profile.sql` — HCP-level aggregation
- `features/feature_store.py` — label extraction and separation
- `models/rule_based_flags.py` — rule engine (fires on features, not on violation columns)
- `models/scorer.py` — rule_score computation

---

## How `max_severity` is constructed

`ground_truth_max_severity` is a two-step derivation: record-level violation
detection during synthetic data generation, then HCP-level aggregation in dbt.

**Step 1 — Record-level detection (`synthetic_generator.py`, `apply_violation_flags()`):**

Each interaction and speaker event record is evaluated against compliance rules
checked on raw record fields. `violation_severity` is set per record:

```python
# synthetic_generator.py lines 327–346
def _severity_from_types(violation_types: list[str]) -> str:
    HIGH = {
        "SPEAKER_VENUE_INAPPROPRIATE", "SPEAKER_FEE_EXCEEDS_FMV",
        "SPEAKER_SELECTED_BY_PRESCRIBING", "CMS_RECONCILIATION_GAP",
        "NON_HCP_ATTENDEES", "ANNUAL_COMPENSATION_CAP_EXCEEDED",
        "ATTENDEE_SAME_OFFICE_AS_SPEAKER",
    }
    MEDIUM = {
        "MEAL_COST_EXCESSIVE", "REPEAT_PROGRAM_ATTENDANCE", "LOW_ATTENDEE_COUNT",
        "ALCOHOL_PROVIDED", "RAPID_INTERACTION_PATTERN", "REPEAT_SAME_TOPIC_PROGRAMS",
        "SPEAKER_RAPID_REPEAT",
    }
    if not violation_types:
        return "none"
    s = set(violation_types)
    if s & HIGH:
        return "high"
    if s & MEDIUM:
        return "medium"
    return "low"

# lines 2153–2157
df["violation_types"] = [",".join(vt[i]) for i in range(len(df))]
df["violation_severity"] = df["violation_types"].apply(
    lambda v: _severity_from_types(v.split(",") if v else [])
)
df["is_violation"] = df["violation_types"].apply(lambda v: bool(v))
```

The inputs are raw record fields — `meal_cost`, `annual_total_ytd`,
`fmv_exceeded`, `venue_type`, `attendee_count`, etc. — not any downstream
feature or rule flag. These three columns are stored in the raw parquet output.

**Step 2 — HCP-level aggregation (`mart_hcp_risk_profile.sql`, `ground_truth_agg` CTE):**

```sql
-- mart_hcp_risk_profile.sql lines 179–203
ground_truth_agg AS (
    SELECT
        hcp_id,
        COUNT(CASE WHEN is_violation = true THEN 1 END) AS ground_truth_violation_count,
        CASE MAX(
            CASE violation_severity
                WHEN 'none'   THEN 0
                WHEN 'low'    THEN 1
                WHEN 'medium' THEN 2
                WHEN 'high'   THEN 3
                ELSE 0
            END
        )
            WHEN 3 THEN 'high'
            WHEN 2 THEN 'medium'
            WHEN 1 THEN 'low'
            ELSE 'none'
        END AS ground_truth_max_severity
    FROM {{ ref('mart_violation_ground_truth') }}
    GROUP BY hcp_id
)
```

`mart_violation_ground_truth` is a simple UNION ALL of `stg_synthetic_interactions`
and `stg_synthetic_speaker_programs` — both are pass-throughs of the raw tables,
selecting `violation_types`, `violation_severity`, `is_violation` directly.
Neither staging model transforms these fields.

**Step 3 — Extraction to parquet (`feature_store.py`, `extract_ground_truth()`):**

```python
# feature_store.py lines 540–555
def extract_ground_truth(df: pd.DataFrame) -> pd.DataFrame:
    gt = df[GROUND_TRUTH_COLS].drop_duplicates(subset=["hcp_id"]).copy()
    gt["has_violation"] = (gt["ground_truth_violation_count"] > 0).astype(int)
    ...
    return gt
```

This function is called before `build_feature_matrix()`. Ground truth columns
are in `EXCLUDE_FROM_FEATURES` and are explicitly dropped from the ML feature
matrix. A label-leakage test in `tests/test_anomaly_models.py` asserts the
three GT columns do not appear in the feature store.

---

## How `has_violation` is constructed

`has_violation` is derived entirely from `ground_truth_violation_count`, which
itself comes from the same record-level violation detection described above:

```python
# feature_store.py line 555
gt["has_violation"] = (gt["ground_truth_violation_count"] > 0).astype(int)
```

It is a binary indicator: 1 if the HCP has at least one violation record of
any severity across all interactions and speaker events, 0 otherwise.

Observed distribution (97,011 HCPs):

| `ground_truth_max_severity` | Count  |
|-----------------------------|--------|
| none                        | 60,992 |
| medium                      | 35,410 |
| high                        |    381 |
| low                         |    228 |

`has_violation == 1` rate: **37.1%** (36,019 HCPs). The dominant contributor is
medium-severity violations (35,410 of 36,019), driven by `MEAL_COST_EXCESSIVE`,
`LOW_ATTENDEE_COUNT`, and similar MEDIUM-tier codes.

---

## How `rule_score` is constructed

`rule_score` is a severity-weighted sum of 23 boolean rule flags fired by
`rule_based_flags.py` on the **aggregated feature store**, not on raw records
or violation columns.

`rule_based_flags.py` reads from `feature_store_raw.parquet` — the pre-scaling
version of the feature matrix, which retains all mart columns including the
ground truth columns. However, the rule flag logic never reads from
`ground_truth_max_severity`, `ground_truth_violation_count`, or `has_violation`.
The flags fire on feature columns such as `meal_breach_rate`,
`annual_cap_pct_used_2022/2023/2024`, `speaker_fee_fmv_pct_mean`, etc.

`scorer.py` then computes `rule_score` as:

```python
# scorer.py lines 228–243
flag_cols = [c for c in ALL_FLAGS if c in merged_df.columns]
weights = np.array([
    SEVERITY_WEIGHTS.get(FLAG_SEVERITY.get(c, "medium"), 10)
    for c in flag_cols
], dtype=np.float32)
# critical=40 pts, high=20 pts, medium=10 pts
flag_matrix = merged_df[flag_cols].astype(np.float32).values
raw_rule_score = flag_matrix @ weights
rule_score = np.minimum(raw_rule_score, 100.0)
```

The severity weights (`critical=40`, `high=20`, `medium=10`) in `scorer.py` are
independent from the severity categories in `_severity_from_types()` in
`synthetic_generator.py`. They use the same vocabulary (`high`, `medium`) but
represent different things: the scorer weights the importance of rule flags;
the generator classifies the type of compliance violation.

---

## Overlap statistics

Computed on 97,011 HCPs (inner join of `ground_truth_labels.parquet` ×
`risk_scores.parquet`):

| Question | Result |
|---|---|
| Of 381 HCPs with `max_severity == 'high'`, fraction with `rule_score > 0` | **99.0%** |
| Of 13,974 HCPs with `rule_score > 0`, fraction with `has_violation == 1` | **75.5%** |

Additional context:
- HCPs with `rule_score == 0`: 83,037 (85.6%)
- HCPs with `rule_score > 0`: 13,974 (14.4%)
- Overall `has_violation` rate: 37.1% (36,019 HCPs)

The 99% figure means the rule engine almost never misses a high-severity HCP.
The 75.5% figure means 24.5% of rule-flagged HCPs have no ground truth
violation — the rule engine fires on some HCPs the generator did not classify
as violators.

---

## Verdict

**HYBRID** — the evaluation is not circular in the strict sense, but the
correlation between `rule_score` and the ground truth labels is structurally
elevated by design. The precise relationship is:

**Ground truth and rule flags both check the same underlying compliance facts
via independent code paths at different levels of aggregation.**

- The ground truth is computed in `synthetic_generator.py` at *record level*,
  checking raw fields (`meal_cost`, `annual_total_ytd`, `fmv_exceeded`,
  `venue_type`) against the same compliance thresholds (PhRMA Code, OIG Special
  Fraud Alert 2020, Sunshine Act) that the rule engine uses.
- The rule flags fire at *HCP level* on aggregated features derived from those
  same records (`meal_breach_rate`, `annual_cap_pct_used_*`,
  `speaker_fee_fmv_pct_mean`).
- The derivation paths are different code (different files, different
  granularity, no shared function calls). `rule_score` is not copied from
  violation columns, and ground truth is not copied from rule_flags.

**What this means for interpretation:**

1. **The 99% recall on high-severity is not a surprise** — it is expected.
   An HCP whose raw records trigger `ANNUAL_COMPENSATION_CAP_EXCEEDED`
   (ground truth) will almost certainly have `annual_cap_pct_used_* > 1.0`
   in features, which fires `flag_annual_cap_breach_*`, which elevates
   `rule_score`. The rules were calibrated to the same thresholds, so
   near-perfect recall on high-severity is a structural property of the
   dataset, not a performance achievement.

2. **The rules-only recall figure against `has_violation` is a genuine
   measure of calibration**, in that `rule_score > 0` and `has_violation == 1`
   are computed by genuinely independent logic. But the magnitude of that
   recall is inflated relative to what it would be on real-world data, because
   in this synthetic dataset every violation was generated to be detectable by
   exactly the rule categories the rule engine checks.

3. **The IF and COPOD evaluations are fully independent** — neither model has
   any access to the violation logic, the rule flags, or the ground truth
   columns during training. Their performance against the ground truth labels
   is a genuine measure of whether unsupervised anomaly detection surfaces the
   same HCPs the compliance rules identify.

4. **The comparison between IF/COPOD and rule_score against `has_violation`
   should be interpreted as**: "how well does each method agree with a
   record-level compliance audit," not "how well does each method detect
   violations an independent auditor would find." Both the ground truth and
   the rule engine are implementations of the same Nova Pharma compliance
   rulebook applied to the same synthetic dataset.

**What would fully resolve ambiguity:** The ground truth would be purely
independent if the HCP violation profile (`clean` / `minor` / `moderate` /
`serious`, assigned at generation time in `generate_hcp_master()`) were used
directly as the label, rather than the post-hoc `apply_violation_flags()`
output. The profile is the truly latent ground truth; the violation flags are
a re-evaluation of the generated data against compliance rules — the same rules
the rule engine also checks.
