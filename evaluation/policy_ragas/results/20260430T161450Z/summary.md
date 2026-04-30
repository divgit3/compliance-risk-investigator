# Policy RAGAS Baseline

**Generated:** 2026-04-30T16:14:50.822065+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.851 | 0.700 | 0.467 | 0.792 | 0/4 | 4/4 |
| retrieval | 5 | 0.872 | 0.532 | 0.950 | 0.867 | 0/5 | 4/5 |
| unanswerable | 2 | 0.750 | 0.000 | 0.000 | 0.333 | 0/2 | 1/2 |
| false_premise | 3 | 0.574 | 0.701 | 0.838 | 0.722 | 2/3 | 2/3 |
| registry_gap | 2 | 0.736 | 0.749 | 1.000 | 0.500 | 0/2 | 1/2 |
| **ALL** | **16** | **0.779** | **0.567** | **0.696** | **0.708** | **2/16** | **12/16** |

## Notable Observations

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not explicitly state the specific criminal penalties for violating the federal anti-kickback statute, nor does it mention liability under the False Claims Act in the context of penalties.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The retrieved content provides specific rules regarding the distribution of drug samples under the Prescription Drug Marketing Act, which contradicts the claim that the policy does not address specific rules.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about a per-specialty compensation cap for cardiologists or confirm the application of the $75,000 cap uniformly across all specialties.
- RAGAS Faithfulness=0.375 < 0.5
- safety net fired (prepended to data_limitations)

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The retrieved content specifies an annual meal cap of $500, contradicting the claim that there is no annual meal-specific cap, and the $75,000 cap is for speaker fees, not total compensation including meals.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.851 | ✓ |
| answer_relevancy | ≥0.7 | 0.700 | ✓ |
| context_precision | ≥0.5 | 0.467 | ✗ |
| latency_p95_ms | ≤15000ms | 8571ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-30T05:03:59.704671+00:00  
**Current:**  2026-04-30T16:14:50.822065+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.816 → 0.851 (+0.036) | 0.700 → 0.700 (+0.000) | 0.532 → 0.467 (-0.065) | 0.792 → 0.792 (+0.000) |
| retrieval | 0.654 → 0.872 (+0.218) | 0.532 → 0.532 (+0.000) | 0.953 → 0.950 (-0.003) | 0.867 → 0.867 (+0.000) |
| unanswerable | 0.875 → 0.750 (-0.125) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| false_premise | 0.818 → 0.574 (-0.245) | 0.179 → 0.701 (+0.522) | 0.892 → 0.838 (-0.054) | 0.889 → 0.722 (-0.167) |
| registry_gap | 0.577 → 0.736 (+0.159) | 0.830 → 0.749 (-0.081) | 1.000 → 1.000 (+0.000) | 0.333 → 0.500 (+0.167) |
| **ALL** | 0.764 → 0.779 (+0.015) | 0.479 → 0.567 (+0.088) | 0.723 → 0.696 (-0.028) | 0.719 → 0.708 (-0.010) |

