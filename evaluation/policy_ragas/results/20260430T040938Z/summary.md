# Policy RAGAS Baseline

**Generated:** 2026-04-30T04:09:38.558677+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.830 | 0.700 | 0.478 | 0.667 | 0/4 | 4/4 |
| retrieval | 5 | 0.697 | 0.532 | 0.956 | 0.867 | 0/5 | 4/5 |
| unanswerable | 2 | 0.833 | 0.000 | 0.000 | 0.000 | 0/2 | 0/2 |
| false_premise | 3 | 0.850 | 0.373 | 0.616 | 0.472 | 2/3 | 2/3 |
| registry_gap | 2 | 0.583 | 0.830 | 1.000 | 0.333 | 0/2 | 1/2 |
| **ALL** | **16** | **0.772** | **0.515** | **0.659** | **0.568** | **2/16** | **11/16** |

## Notable Observations

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not specify the criminal penalties or mention the False Claims Act in relation to the anti-kickback statute, making these claims ungrounded.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The answer makes several claims about the absence of specific telehealth policies and interpretations of retrieved content that are not directly supported by the retrieved excerpts.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The answer makes claims about the absence of specific rules in Nova Pharma's policy, which cannot be verified as the retrieved content does not provide Nova Pharma-specific rules or a rules registry for comparison.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about state-specific meal limits or PhRMA's exact meal thresholds, making these claims ungrounded.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- safety net fired (prepended to data_limitations)

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The retrieved content specifies an annual meal cap of $500, contradicting the claim that there is no annual meal-specific cap, and the $75,000 cap is for speaker fees, not total compensation including meals.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.830 | ✓ |
| answer_relevancy | ≥0.7 | 0.700 | ✓ |
| context_precision | ≥0.5 | 0.478 | ✗ |
| latency_p95_ms | ≤15000ms | 7432ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-28T11:49:08.200071+00:00  
**Current:**  2026-04-30T04:09:38.558677+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.698 → 0.830 (+0.132) | 0.674 → 0.700 (+0.026) | 0.547 → 0.478 (-0.070) | 0.667 → 0.667 (+0.000) |
| retrieval | 0.685 → 0.697 (+0.011) | 0.526 → 0.532 (+0.006) | 0.945 → 0.956 (+0.011) | 0.889 → 0.867 (-0.022) |
| unanswerable | 0.885 → 0.833 (-0.051) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.667 → 0.000 (-0.667) |
| false_premise | 1.000 → 0.850 (-0.150) | 0.414 → 0.373 (-0.041) | 0.778 → 0.616 (-0.162) | 0.778 → 0.472 (-0.306) |
| registry_gap | 0.748 → 0.583 (-0.164) | 0.843 → 0.830 (-0.013) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.802 → 0.772 (-0.030) | 0.516 → 0.515 (-0.001) | 0.703 → 0.659 (-0.044) | 0.715 → 0.568 (-0.148) |

