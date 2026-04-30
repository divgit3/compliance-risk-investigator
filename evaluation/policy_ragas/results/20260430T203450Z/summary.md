# Policy RAGAS Baseline

**Generated:** 2026-04-30T20:34:50.553725+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.717 | 0.675 | 0.426 | 0.792 | 0/4 | 4/4 |
| retrieval | 5 | 0.881 | 0.561 | 0.950 | 0.889 | 0/5 | 4/5 |
| unanswerable | 2 | 1.000 | 0.000 | 0.000 | 0.333 | 0/2 | 0/2 |
| false_premise | 3 | 0.877 | 0.200 | 0.616 | 0.667 | 2/3 | 2/3 |
| registry_gap | 2 | 0.675 | 0.795 | 1.000 | 0.333 | 0/2 | 1/2 |
| **ALL** | **16** | **0.829** | **0.481** | **0.644** | **0.684** | **2/16** | **11/16** |

## Notable Observations

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not explicitly mention specific criminal penalties or the False Claims Act in relation to the anti-kickback statute violations.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not explicitly address telehealth-only HCP interactions or confirm that meal limits and compensation caps apply uniformly to telehealth interactions.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The answer makes claims about the absence of specific Nova Pharma rules and thresholds, which are not supported by the retrieved content as no specific Nova Pharma rules were provided.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The answer includes specific claims about meal limits and comparisons that are not directly supported by the retrieved content.
- safety net fired (scope mismatch)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- safety net fired (scope mismatch)

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The retrieved content specifies an annual meal cap of $500 and an annual compensation cap of $75,000, but it does not state that the $75,000 cap includes meals, nor does it say there is no annual meal-specific cap.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.717 | ✓ |
| answer_relevancy | ≥0.7 | 0.675 | ✗ |
| context_precision | ≥0.5 | 0.426 | ✗ |
| latency_p95_ms | ≤15000ms | 14873ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-30T19:13:43.747526+00:00  
**Current:**  2026-04-30T20:34:50.553725+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.782 → 0.717 (-0.065) | 0.674 → 0.675 (+0.001) | 0.426 → 0.426 (+0.000) | 0.792 → 0.792 (+0.000) |
| retrieval | 0.762 → 0.881 (+0.119) | 0.532 → 0.561 (+0.029) | 0.954 → 0.950 (-0.005) | 0.833 → 0.889 (+0.056) |
| unanswerable | 0.750 → 1.000 (+0.250) | 0.000 → 0.000 (+0.000) | 0.167 → 0.000 (-0.167) | 0.000 → 0.333 (+0.333) |
| false_premise | 0.796 → 0.877 (+0.081) | 0.469 → 0.200 (-0.269) | 0.778 → 0.616 (-0.162) | 0.889 → 0.667 (-0.222) |
| registry_gap | 0.542 → 0.675 (+0.134) | 0.828 → 0.795 (-0.033) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.744 → 0.829 (+0.084) | 0.526 → 0.481 (-0.045) | 0.696 → 0.644 (-0.053) | 0.667 → 0.684 (+0.017) |

