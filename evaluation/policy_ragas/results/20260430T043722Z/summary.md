# Policy RAGAS Baseline

**Generated:** 2026-04-30T04:37:22.321639+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.865 | 0.667 | 0.410 | 0.667 | 0/4 | 4/4 |
| retrieval | 5 | 0.286 | 0.532 | 0.945 | 0.845 | 0/5 | 4/5 |
| unanswerable | 2 | 0.750 | 0.000 | 0.000 | 0.333 | 0/2 | 0/2 |
| false_premise | 3 | 0.833 | 0.684 | 0.694 | 0.889 | 2/3 | 3/3 |
| registry_gap | 2 | 0.538 | 0.685 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.714** | **0.547** | **0.653** | **0.681** | **2/16** | **13/16** |

## Notable Observations

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content explicitly states the criminal penalties for violating the federal anti-kickback statute, including a maximum fine of $100,000 and imprisonment up to 10 years, which contradicts the claim in the answer.
- RAGAS Faithfulness=0.000 < 0.5

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The answer makes several claims about the absence of specific telehealth policies and interpretations of the retrieved documents that are not directly supported by the retrieved content.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The retrieved content provides specific rules regarding the labeling and distribution of drug samples under the PDMA, contradicting the claim that the policy does not address specific rules.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.865 | ✓ |
| answer_relevancy | ≥0.7 | 0.667 | ✗ |
| context_precision | ≥0.5 | 0.410 | ✗ |
| latency_p95_ms | ≤15000ms | 12586ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-28T11:49:08.200071+00:00  
**Current:**  2026-04-30T04:37:22.321639+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.698 → 0.865 (+0.166) | 0.674 → 0.667 (-0.007) | 0.547 → 0.410 (-0.137) | 0.667 → 0.667 (+0.000) |
| retrieval | 0.685 → 0.286 (-0.399) | 0.526 → 0.532 (+0.006) | 0.945 → 0.945 (+0.000) | 0.889 → 0.845 (-0.044) |
| unanswerable | 0.885 → 0.750 (-0.135) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.667 → 0.333 (-0.333) |
| false_premise | 1.000 → 0.833 (-0.167) | 0.414 → 0.684 (+0.269) | 0.778 → 0.694 (-0.083) | 0.778 → 0.889 (+0.111) |
| registry_gap | 0.748 → 0.538 (-0.209) | 0.843 → 0.685 (-0.158) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.802 → 0.714 (-0.088) | 0.516 → 0.547 (+0.031) | 0.703 → 0.653 (-0.050) | 0.715 → 0.681 (-0.035) |

