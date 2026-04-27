# Policy RAGAS Baseline

**Generated:** 2026-04-27T08:58:21.289358+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.846 | 0.710 | 0.381 | 0.667 | 0/4 | 2/4 |
| retrieval | 5 | 0.537 | 0.559 | 0.419 | 0.267 | 0/5 | 0/5 |
| unanswerable | 2 | 0.833 | 0.000 | 0.000 | 0.000 | 0/2 | 0/2 |
| false_premise | 3 | 0.708 | 0.420 | 0.790 | 0.333 | 2/3 | 1/3 |
| registry_gap | 2 | 1.000 | 0.281 | 0.500 | 0.167 | 0/2 | 0/2 |
| **ALL** | **16** | **0.740** | **0.466** | **0.437** | **0.333** | **2/16** | **3/16** |

## Notable Observations

### `rb_02_speaker_fmv_ceiling` (rule_backed)
> What is the speaker FMV ceiling?
- groundedness_check.grounded=False — The retrieved content does not provide specific numeric thresholds for the speaker FMV ceiling from the PhRMA Code, nor does it confirm the $3,500 limit per engagement for Nova Pharma; the retrieved rule only mentions a $75,000 annual cap.

### `rb_04_meal_per_attendee_ceiling` (rule_backed)
> What is the per-attendee meal cost ceiling at speaker events?
- groundedness_check.grounded=False — The answer's claims about meal cost ceilings and limits are grounded in the retrieved rules, but the specific document citations provided in the answer do not match any retrieved content.

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not explicitly support the specific claims made about OIG's identified characteristics of potentially violative speaker programs.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not provide specific information on the criminal penalties for violating the federal anti-kickback statute, nor does it mention fines or imprisonment, making these claims ungrounded.

### `ret_03_phrma_sales_meal_conditions` (retrieval)
> Under the PhRMA Code, what conditions must be met for a sales representative to offer a meal to an HCP?
- groundedness_check.grounded=False — The retrieved content does not provide specific meal limits under the PhRMA Code, nor does it confirm that these limits are enforced per meal.

### `ret_04_phrma_educational_items_threshold` (retrieval)
> Under the PhRMA Code, what is the value threshold for educational items that can be provided to HCPs, and what conditions apply?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about value thresholds for educational items, conditions for educational items, or comparisons between Nova Pharma and PhRMA guidelines.
- RAGAS Faithfulness=0.308 < 0.5

### `ret_05_oig_compliance_program_elements` (retrieval)
> What are the seven elements of an effective pharmaceutical compliance program according to OIG guidance?
- groundedness_check.grounded=False — The retrieved content does not provide specific details about the seven elements of an effective pharmaceutical compliance program, nor does it mention Nova Pharma or its policies.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not provide any information about Nova Pharma's policy on telehealth-only HCP interactions, nor does it confirm the absence of such a policy.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The answer makes specific claims about the absence of rules and details in the retrieved content, but these claims are not directly supported by the retrieved excerpts, which do not explicitly address Nova Pharma's rules or thresholds for drug sample distribution.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not provide information on specialty-specific caps or comparisons with PhRMA guidelines, making these claims ungrounded.
- RAGAS Faithfulness=0.417 < 0.5
- safety net fired (prepended to data_limitations)

### `fp_03_quarterly_speaker_fee_limit` (false_premise)
> What is the quarterly speaker fee limit?
- groundedness_check.grounded=False — The retrieved content does not explicitly mention the absence of a quarterly speaker fee limit or the lack of a cap on the number of engagements per quarter.

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The retrieved content does not explicitly state that there is no annual meal-specific cap, nor does it confirm that the $75,000 cap includes all forms of compensation, including meals.

### `rg_02_office_visit_frequency_by_hcp_type` (registry_gap)
> What are Nova Pharma's office visit frequency limits per HCP per year, by HCP type?
- groundedness_check.grounded=False — The retrieved content does not provide information about office visit frequency limits per HCP per year, segmented by HCP type, making the claim ungrounded.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.846 | ✓ |
| answer_relevancy | ≥0.7 | 0.710 | ✓ |
| context_precision | ≥0.5 | 0.381 | ✗ |
| latency_p95_ms | ≤15000ms | 8949ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-27T08:29:57.843120+00:00  
**Current:**  2026-04-27T08:58:21.289358+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.719 → 0.846 (+0.127) | 0.735 → 0.710 (-0.025) | 0.360 → 0.381 (+0.020) | 0.542 → 0.667 (+0.125) |
| retrieval | 1.000 → 0.537 (-0.463) | 0.184 → 0.559 (+0.375) | 0.486 → 0.419 (-0.067) | 0.500 → 0.267 (-0.233) |
| unanswerable | 0.514 → 0.833 (+0.320) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) |
| false_premise | 0.811 → 0.708 (-0.103) | 0.442 → 0.420 (-0.023) | 0.588 → 0.790 (+0.202) | 0.333 → 0.333 (+0.000) |
| registry_gap | 1.000 → 1.000 (+0.000) | 0.397 → 0.281 (-0.116) | 1.000 → 0.500 (-0.500) | 0.000 → 0.167 (+0.167) |
| **ALL** | 0.823 → 0.740 (-0.083) | 0.374 → 0.466 (+0.092) | 0.477 → 0.437 (-0.040) | 0.354 → 0.333 (-0.021) |

