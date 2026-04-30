# Policy RAGAS Baseline

**Generated:** 2026-04-30T16:44:21.938463+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.718 | 0.675 | 0.467 | 0.667 | 0/4 | 4/4 |
| retrieval | 5 | 0.867 | 0.337 | 0.945 | 0.867 | 0/5 | 3/5 |
| unanswerable | 2 | 0.667 | 0.000 | 0.167 | 0.667 | 0/2 | 1/2 |
| false_premise | 3 | 0.659 | 0.648 | 0.616 | 0.500 | 2/3 | 1/3 |
| registry_gap | 2 | 0.791 | 0.770 | 1.000 | 0.333 | 0/2 | 1/2 |
| **ALL** | **16** | **0.756** | **0.492** | **0.673** | **0.656** | **2/16** | **10/16** |

## Notable Observations

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The claims about Nova Pharma's internal policies and thresholds are not supported by the retrieved content, which only discusses OIG guidelines and not specific company policies.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not explicitly mention the specific criminal penalties or the connection to the False Claims Act, making these claims ungrounded.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The retrieved content provides general guidance on drug sample distribution but does not specify Nova Pharma's rules, making the claim ungrounded.
- RAGAS Faithfulness=0.333 < 0.5

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The retrieved content does not explicitly state that meal limits apply uniformly across all states or provide PhRMA equivalent values for meal limits.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not provide any information about per-specialty compensation caps or confirm the application of the $75,000 cap uniformly across all specialties, including cardiologists.
- RAGAS Faithfulness=0.375 < 0.5
- safety net fired (prepended to data_limitations)

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The retrieved content specifies an annual meal cap of $500, contradicting the claim that there is no annual meal-specific cap, and the $75,000 cap is for speaker fees, not total compensation including meals.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.718 | ✓ |
| answer_relevancy | ≥0.7 | 0.675 | ✗ |
| context_precision | ≥0.5 | 0.467 | ✗ |
| latency_p95_ms | ≤15000ms | 16571ms | ✗ |

## Delta from Previous Baseline

**Previous:** 2026-04-30T16:14:50.822065+00:00  
**Current:**  2026-04-30T16:44:21.938463+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.851 → 0.718 (-0.134) | 0.700 → 0.675 (-0.025) | 0.467 → 0.467 (-0.000) | 0.792 → 0.667 (-0.125) |
| retrieval | 0.872 → 0.867 (-0.005) | 0.532 → 0.337 (-0.195) | 0.950 → 0.945 (-0.005) | 0.867 → 0.867 (+0.000) |
| unanswerable | 0.750 → 0.667 (-0.083) | 0.000 → 0.000 (+0.000) | 0.000 → 0.167 (+0.167) | 0.333 → 0.667 (+0.333) |
| false_premise | 0.574 → 0.659 (+0.086) | 0.701 → 0.648 (-0.053) | 0.838 → 0.616 (-0.222) | 0.722 → 0.500 (-0.222) |
| registry_gap | 0.736 → 0.791 (+0.054) | 0.749 → 0.770 (+0.020) | 1.000 → 1.000 (+0.000) | 0.500 → 0.333 (-0.167) |
| **ALL** | 0.779 → 0.756 (-0.022) | 0.567 → 0.492 (-0.075) | 0.696 → 0.673 (-0.022) | 0.708 → 0.656 (-0.052) |

