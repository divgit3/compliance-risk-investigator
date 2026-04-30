# Policy RAGAS Baseline

**Generated:** 2026-04-30T19:13:43.747526+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.782 | 0.674 | 0.426 | 0.792 | 0/4 | 3/4 |
| retrieval | 5 | 0.762 | 0.532 | 0.954 | 0.833 | 1/5 | 4/5 |
| unanswerable | 2 | 0.750 | 0.000 | 0.167 | 0.000 | 1/2 | 0/2 |
| false_premise | 3 | 0.796 | 0.469 | 0.778 | 0.889 | 2/3 | 3/3 |
| registry_gap | 2 | 0.542 | 0.828 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.744** | **0.526** | **0.696** | **0.667** | **4/16** | **12/16** |

## Notable Observations

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- groundedness_check.grounded=False — The retrieved content does not provide information about the PhRMA Code setting a similar cap or Nova Pharma's policy being stricter in comparison to the PhRMA Code.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content explicitly states the criminal penalties for violating the federal anti-kickback statute, including a maximum fine of $100,000 and imprisonment up to 10 years, which contradicts the claim in the answer.
- RAGAS Faithfulness=0.000 < 0.5
- safety net fired (over-narration stripped)

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The answer includes claims about telehealth-only interactions and general principles from the OIG and PhRMA that are not explicitly supported by the retrieved content.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The retrieved content provides specific rules regarding the distribution of drug samples under the Prescription Drug Marketing Act, which contradicts the claim that the policy does not address specific rules.
- safety net fired (over-narration stripped)

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (scope mismatch)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- safety net fired (scope mismatch)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.782 | ✓ |
| answer_relevancy | ≥0.7 | 0.674 | ✗ |
| context_precision | ≥0.5 | 0.426 | ✗ |
| latency_p95_ms | ≤15000ms | 10842ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-30T16:44:21.938463+00:00  
**Current:**  2026-04-30T19:13:43.747526+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.718 → 0.782 (+0.065) | 0.675 → 0.674 (-0.001) | 0.467 → 0.426 (-0.041) | 0.667 → 0.792 (+0.125) |
| retrieval | 0.867 → 0.762 (-0.105) | 0.337 → 0.532 (+0.195) | 0.945 → 0.954 (+0.009) | 0.867 → 0.833 (-0.033) |
| unanswerable | 0.667 → 0.750 (+0.083) | 0.000 → 0.000 (+0.000) | 0.167 → 0.167 (+0.000) | 0.667 → 0.000 (-0.667) |
| false_premise | 0.659 → 0.796 (+0.137) | 0.648 → 0.469 (-0.179) | 0.616 → 0.778 (+0.162) | 0.500 → 0.889 (+0.389) |
| registry_gap | 0.791 → 0.542 (-0.249) | 0.770 → 0.828 (+0.058) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.756 → 0.744 (-0.012) | 0.492 → 0.526 (+0.034) | 0.673 → 0.696 (+0.023) | 0.656 → 0.667 (+0.010) |

