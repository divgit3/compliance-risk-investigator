# Policy RAGAS Baseline

**Generated:** 2026-04-30T17:40:50.609353+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.812 | 0.700 | 0.410 | 0.792 | 0/4 | 4/4 |
| retrieval | 5 | 0.829 | 0.532 | 0.950 | 0.867 | 0/5 | 3/5 |
| unanswerable | 2 | 0.667 | 0.000 | 0.000 | 0.333 | 0/2 | 0/2 |
| false_premise | 3 | 0.713 | 0.200 | 0.789 | 0.583 | 2/3 | 1/3 |
| registry_gap | 2 | 0.613 | 0.819 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.756** | **0.481** | **0.672** | **0.661** | **2/16** | **10/16** |

## Notable Observations

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The claims about Nova Pharma's internal policies and specific thresholds are not supported by the retrieved content, which does not mention Nova Pharma or its policies.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not provide specific details on criminal penalties for violating the anti-kickback statute, nor does it confirm the claims about civil monetary sanctions, exclusion, or the determination of liability based on intent.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not explicitly mention Nova Pharma's policy on telehealth-only HCP interactions, nor does it confirm that general rules apply uniformly to such interactions.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The retrieved content provides general compliance considerations related to drug sample distribution but does not explicitly state that specific rules for distributing drug samples to HCPs are not addressed.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The retrieved content does not explicitly state that meal limits apply uniformly across all states, including California, nor does it provide the specific meal limits for Nova Pharma or PhRMA as mentioned in the answer.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not specifically mention a per-specialty compensation cap for cardiologists or confirm the $75,000 cap applies uniformly across all specialties, including cardiologists.
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.812 | ✓ |
| answer_relevancy | ≥0.7 | 0.700 | ✓ |
| context_precision | ≥0.5 | 0.410 | ✗ |
| latency_p95_ms | ≤15000ms | 7755ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-30T16:44:21.938463+00:00  
**Current:**  2026-04-30T17:40:50.609353+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.718 → 0.812 (+0.095) | 0.675 → 0.700 (+0.025) | 0.467 → 0.410 (-0.057) | 0.667 → 0.792 (+0.125) |
| retrieval | 0.867 → 0.829 (-0.038) | 0.337 → 0.532 (+0.195) | 0.945 → 0.950 (+0.005) | 0.867 → 0.867 (+0.000) |
| unanswerable | 0.667 → 0.667 (+0.000) | 0.000 → 0.000 (+0.000) | 0.167 → 0.000 (-0.167) | 0.667 → 0.333 (-0.333) |
| false_premise | 0.659 → 0.713 (+0.054) | 0.648 → 0.200 (-0.448) | 0.616 → 0.789 (+0.174) | 0.500 → 0.583 (+0.083) |
| registry_gap | 0.791 → 0.613 (-0.177) | 0.770 → 0.819 (+0.050) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.756 → 0.756 (-0.000) | 0.492 → 0.481 (-0.011) | 0.673 → 0.672 (-0.001) | 0.656 → 0.661 (+0.005) |

