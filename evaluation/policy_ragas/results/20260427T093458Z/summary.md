# Policy RAGAS Baseline

**Generated:** 2026-04-27T09:34:58.689552+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.815 | 0.675 | 0.454 | 0.792 | 0/4 | 4/4 |
| retrieval | 5 | 0.333 | 0.732 | 0.745 | 0.733 | 0/5 | 4/4 |
| unanswerable | 2 | 0.822 | 0.000 | 0.000 | 0.333 | 0/2 | 1/2 |
| false_premise | 3 | 0.801 | 0.200 | 0.815 | 0.889 | 2/3 | 3/3 |
| registry_gap | 2 | 0.962 | 0.825 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.761** | **0.538** | **0.624** | **0.677** | **2/16** | **14/15** |

## Notable Observations

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- RAGAS Faithfulness=0.000 < 0.5

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not specifically mention telehealth-only HCP interactions or the absence of dimensions for jurisdiction, specialty, and role in the policy.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.815 | ✓ |
| answer_relevancy | ≥0.7 | 0.675 | ✗ |
| context_precision | ≥0.5 | 0.454 | ✗ |
| latency_p95_ms | ≤15000ms | 8999ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-27T08:58:21.289358+00:00  
**Current:**  2026-04-27T09:34:58.689552+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.846 → 0.815 (-0.030) | 0.710 → 0.675 (-0.035) | 0.381 → 0.454 (+0.073) | 0.667 → 0.792 (+0.125) |
| retrieval | 0.537 → 0.333 (-0.204) | 0.559 → 0.732 (+0.173) | 0.419 → 0.745 (+0.326) | 0.267 → 0.733 (+0.467) |
| unanswerable | 0.833 → 0.822 (-0.011) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.000 → 0.333 (+0.333) |
| false_premise | 0.708 → 0.801 (+0.092) | 0.420 → 0.200 (-0.220) | 0.790 → 0.815 (+0.024) | 0.333 → 0.889 (+0.556) |
| registry_gap | 1.000 → 0.962 (-0.038) | 0.281 → 0.825 (+0.544) | 0.500 → 1.000 (+0.500) | 0.167 → 0.333 (+0.167) |
| **ALL** | 0.740 → 0.761 (+0.022) | 0.466 → 0.538 (+0.072) | 0.437 → 0.624 (+0.187) | 0.333 → 0.677 (+0.344) |

