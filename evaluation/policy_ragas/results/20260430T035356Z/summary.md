# Policy RAGAS Baseline

**Generated:** 2026-04-30T03:53:56.871159+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.721 | 0.699 | 0.467 | 0.792 | 0/4 | 4/4 |
| retrieval | 5 | 0.909 | 0.501 | 0.950 | 0.933 | 0/5 | 4/5 |
| unanswerable | 2 | 0.844 | 0.000 | 0.000 | 0.667 | 0/2 | 1/2 |
| false_premise | 3 | 0.657 | 0.391 | 0.589 | 0.889 | 2/3 | 1/3 |
| registry_gap | 2 | 0.613 | 0.822 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.750** | **0.507** | **0.649** | **0.781** | **2/16** | **12/16** |

## Notable Observations

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- RAGAS Faithfulness=0.444 < 0.5

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content mentions potential penalties but does not specify criminal penalties or liability under the False Claims Act as stated in the answer.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not explicitly address telehealth-only HCP interactions or provide specific guidelines or thresholds for such interactions, making the claims ungrounded.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The retrieved content does not explicitly state that meal limits are not segmented by state or that they apply uniformly across jurisdictions, nor does it provide specific PhRMA meal limits for breakfast, lunch, and dinner.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not specifically mention cardiologists or a per-specialty compensation cap, nor does it compare Nova Pharma's policy to typical industry standards or the PhRMA Code.
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.721 | ✓ |
| answer_relevancy | ≥0.7 | 0.699 | ✗ |
| context_precision | ≥0.5 | 0.467 | ✗ |
| latency_p95_ms | ≤15000ms | 10462ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-28T11:49:08.200071+00:00  
**Current:**  2026-04-30T03:53:56.871159+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.698 → 0.721 (+0.023) | 0.674 → 0.699 (+0.024) | 0.547 → 0.467 (-0.080) | 0.667 → 0.792 (+0.125) |
| retrieval | 0.685 → 0.909 (+0.224) | 0.526 → 0.501 (-0.025) | 0.945 → 0.950 (+0.005) | 0.889 → 0.933 (+0.044) |
| unanswerable | 0.885 → 0.844 (-0.041) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.667 → 0.667 (+0.000) |
| false_premise | 1.000 → 0.657 (-0.343) | 0.414 → 0.391 (-0.023) | 0.778 → 0.589 (-0.189) | 0.778 → 0.889 (+0.111) |
| registry_gap | 0.748 → 0.613 (-0.135) | 0.843 → 0.822 (-0.021) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.802 → 0.750 (-0.052) | 0.516 → 0.507 (-0.009) | 0.703 → 0.649 (-0.054) | 0.715 → 0.781 (+0.066) |

