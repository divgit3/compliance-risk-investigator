# Policy RAGAS Baseline

**Generated:** 2026-04-28T11:28:43.026060+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.858 | 0.671 | 0.519 | 0.792 | 0/4 | 4/4 |
| retrieval | 5 | 0.718 | 0.348 | 0.950 | 0.867 | 0/5 | 3/5 |
| unanswerable | 2 | 0.964 | 0.000 | 0.000 | 0.000 | 0/2 | 2/2 |
| false_premise | 3 | 0.789 | 0.200 | 0.648 | 0.667 | 2/3 | 1/3 |
| registry_gap | 2 | 0.577 | 0.836 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.794** | **0.419** | **0.673** | **0.635** | **2/16** | **12/16** |

## Notable Observations

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The claims about Nova Pharma's internal policies and specific thresholds are not supported by the retrieved content, which only discusses general OIG fraud indicators related to speaker programs.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not specify the criminal penalties for violating the federal anti-kickback statute, nor does it mention liability under the False Claims Act.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about state-specific meal limits or confirm the uniform application of Nova Pharma's meal limits across all jurisdictions, including California.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not specifically mention a per-specialty compensation cap for cardiologists or confirm the $75,000 cap applies uniformly across all specialties, including cardiologists.
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.858 | ✓ |
| answer_relevancy | ≥0.7 | 0.671 | ✗ |
| context_precision | ≥0.5 | 0.519 | ✓ |
| latency_p95_ms | ≤15000ms | 19007ms | ✗ |

## Delta from Previous Baseline

**Previous:** 2026-04-27T10:16:28.561440+00:00  
**Current:**  2026-04-28T11:28:43.026060+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.719 → 0.858 (+0.139) | 0.681 → 0.671 (-0.009) | 0.426 → 0.519 (+0.094) | 0.792 → 0.792 (+0.000) |
| retrieval | 0.788 → 0.718 (-0.070) | 0.718 → 0.348 (-0.370) | 0.960 → 0.950 (-0.010) | 0.845 → 0.867 (+0.022) |
| unanswerable | 0.837 → 0.964 (+0.128) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.667 → 0.000 (-0.667) |
| false_premise | 0.849 → 0.789 (-0.060) | 0.714 → 0.200 (-0.514) | 0.865 → 0.648 (-0.216) | 0.889 → 0.667 (-0.222) |
| registry_gap | 0.665 → 0.577 (-0.087) | 0.749 → 0.836 (+0.087) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.769 → 0.794 (+0.024) | 0.622 → 0.419 (-0.203) | 0.693 → 0.673 (-0.020) | 0.753 → 0.635 (-0.118) |

