# Policy RAGAS Baseline

**Generated:** 2026-04-30T05:03:59.704671+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.816 | 0.700 | 0.532 | 0.792 | 0/4 | 3/4 |
| retrieval | 5 | 0.654 | 0.532 | 0.953 | 0.867 | 0/5 | 4/5 |
| unanswerable | 2 | 0.875 | 0.000 | 0.000 | 0.333 | 0/2 | 0/2 |
| false_premise | 3 | 0.818 | 0.179 | 0.892 | 0.889 | 2/3 | 2/3 |
| registry_gap | 2 | 0.577 | 0.830 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.764** | **0.479** | **0.723** | **0.719** | **2/16** | **11/16** |

## Notable Observations

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- groundedness_check.grounded=False — The claim about the PhRMA Code setting a similar cap of $75,000 is not supported by the retrieved content, which does not mention the PhRMA Code's specific cap on HCP compensation.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not explicitly mention the specific criminal penalties or the connection to the False Claims Act, making these claims ungrounded.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not specifically mention telehealth-only HCP interactions or provide guidance on this topic, making the claims ungrounded.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The answer makes specific claims about the absence of policy details and interpretations of the PhRMA Code that are not directly supported by the retrieved content.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not specifically mention a per-specialty compensation cap for cardiologists or compare Nova Pharma's policy with PhRMA guidelines, making the claims ungrounded.
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.816 | ✓ |
| answer_relevancy | ≥0.7 | 0.700 | ✓ |
| context_precision | ≥0.5 | 0.532 | ✓ |
| latency_p95_ms | ≤15000ms | 13754ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-28T11:49:08.200071+00:00  
**Current:**  2026-04-30T05:03:59.704671+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.698 → 0.816 (+0.117) | 0.674 → 0.700 (+0.026) | 0.547 → 0.532 (-0.015) | 0.667 → 0.792 (+0.125) |
| retrieval | 0.685 → 0.654 (-0.031) | 0.526 → 0.532 (+0.006) | 0.945 → 0.953 (+0.008) | 0.889 → 0.867 (-0.022) |
| unanswerable | 0.885 → 0.875 (-0.010) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.667 → 0.333 (-0.333) |
| false_premise | 1.000 → 0.818 (-0.182) | 0.414 → 0.179 (-0.235) | 0.778 → 0.892 (+0.115) | 0.778 → 0.889 (+0.111) |
| registry_gap | 0.748 → 0.577 (-0.170) | 0.843 → 0.830 (-0.013) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.802 → 0.764 (-0.038) | 0.516 → 0.479 (-0.037) | 0.703 → 0.723 (+0.020) | 0.715 → 0.719 (+0.003) |

