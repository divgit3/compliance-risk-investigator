# Policy RAGAS Baseline

**Generated:** 2026-04-28T11:49:08.200071+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.698 | 0.674 | 0.547 | 0.667 | 0/4 | 3/4 |
| retrieval | 5 | 0.685 | 0.526 | 0.945 | 0.889 | 0/5 | 4/5 |
| unanswerable | 2 | 0.885 | 0.000 | 0.000 | 0.667 | 0/2 | 1/2 |
| false_premise | 3 | 1.000 | 0.414 | 0.778 | 0.778 | 2/3 | 3/3 |
| registry_gap | 2 | 0.748 | 0.843 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.802** | **0.516** | **0.703** | **0.715** | **2/16** | **13/16** |

## Notable Observations

### `rb_04_meal_per_attendee_ceiling` (rule_backed)
> What is the per-attendee meal cost ceiling at speaker events?
- groundedness_check.grounded=False — The retrieved content does not provide a specific $125 meal cost ceiling from the PhRMA Code, nor does it confirm the $100 ceiling for Nova Pharma with the cited rule ID.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not provide specific details on criminal penalties or mention the False Claims Act in relation to the anti-kickback statute, making these claims ungrounded.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The answer makes several claims about the absence of specific telehealth policies and general guidance from OIG and PhRMA, none of which are directly supported by the retrieved content.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.698 | ✗ |
| answer_relevancy | ≥0.7 | 0.674 | ✗ |
| context_precision | ≥0.5 | 0.547 | ✓ |
| latency_p95_ms | ≤15000ms | 11826ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-27T10:16:28.561440+00:00  
**Current:**  2026-04-28T11:49:08.200071+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.719 → 0.698 (-0.021) | 0.681 → 0.674 (-0.007) | 0.426 → 0.547 (+0.122) | 0.792 → 0.667 (-0.125) |
| retrieval | 0.788 → 0.685 (-0.103) | 0.718 → 0.526 (-0.192) | 0.960 → 0.945 (-0.015) | 0.845 → 0.889 (+0.044) |
| unanswerable | 0.837 → 0.885 (+0.048) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.667 → 0.667 (+0.000) |
| false_premise | 0.849 → 1.000 (+0.151) | 0.714 → 0.414 (-0.300) | 0.865 → 0.778 (-0.087) | 0.889 → 0.778 (-0.111) |
| registry_gap | 0.665 → 0.748 (+0.083) | 0.749 → 0.843 (+0.094) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.769 → 0.802 (+0.033) | 0.622 → 0.516 (-0.106) | 0.693 → 0.703 (+0.009) | 0.753 → 0.715 (-0.038) |

