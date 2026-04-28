# Policy RAGAS Baseline

**Generated:** 2026-04-27T10:16:28.561440+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.719 | 0.681 | 0.426 | 0.792 | 0/4 | 4/4 |
| retrieval | 5 | 0.788 | 0.718 | 0.960 | 0.845 | 0/5 | 4/5 |
| unanswerable | 2 | 0.837 | 0.000 | 0.000 | 0.667 | 0/2 | 1/2 |
| false_premise | 3 | 0.849 | 0.714 | 0.865 | 0.889 | 2/3 | 2/3 |
| registry_gap | 2 | 0.665 | 0.749 | 1.000 | 0.333 | 0/2 | 2/2 |
| **ALL** | **16** | **0.769** | **0.622** | **0.693** | **0.753** | **2/16** | **13/16** |

## Notable Observations

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- RAGAS Faithfulness=0.250 < 0.5

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not provide specific details on criminal penalties for violating the federal anti-kickback statute, nor does it mention the False Claims Act in relation to penalties, making these claims ungrounded.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not explicitly address telehealth-only HCP interactions or provide specific guidelines or thresholds for such interactions, making the claims ungrounded.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not specifically mention cardiologists or a per-specialty cap, and the comparison with PhRMA Code is not supported by the retrieved excerpts.
- safety net fired (prepended to data_limitations)

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.719 | ✓ |
| answer_relevancy | ≥0.7 | 0.681 | ✗ |
| context_precision | ≥0.5 | 0.426 | ✗ |
| latency_p95_ms | ≤15000ms | 10245ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-27T09:34:58.689552+00:00  
**Current:**  2026-04-27T10:16:28.561440+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.815 → 0.719 (-0.096) | 0.675 → 0.681 (+0.005) | 0.454 → 0.426 (-0.028) | 0.792 → 0.792 (+0.000) |
| retrieval | 0.333 → 0.788 (+0.455) | 0.732 → 0.718 (-0.014) | 0.745 → 0.960 (+0.215) | 0.733 → 0.845 (+0.111) |
| unanswerable | 0.822 → 0.837 (+0.014) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.333 → 0.667 (+0.333) |
| false_premise | 0.801 → 0.849 (+0.048) | 0.200 → 0.714 (+0.514) | 0.815 → 0.865 (+0.050) | 0.889 → 0.889 (+0.000) |
| registry_gap | 0.962 → 0.665 (-0.297) | 0.825 → 0.749 (-0.076) | 1.000 → 1.000 (+0.000) | 0.333 → 0.333 (+0.000) |
| **ALL** | 0.761 → 0.769 (+0.008) | 0.538 → 0.622 (+0.084) | 0.624 → 0.693 (+0.069) | 0.677 → 0.753 (+0.076) |

