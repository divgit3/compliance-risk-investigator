# Policy RAGAS Baseline

**Generated:** 2026-04-27T08:29:57.843120+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.719 | 0.735 | 0.360 | 0.542 | 0/4 | 2/4 |
| retrieval | 5 | 1.000 | 0.184 | 0.486 | 0.500 | 0/5 | 0/5 |
| unanswerable | 2 | 0.514 | 0.000 | 0.000 | 0.000 | 0/2 | 0/2 |
| false_premise | 3 | 0.811 | 0.442 | 0.588 | 0.333 | 2/3 | 1/3 |
| registry_gap | 2 | 1.000 | 0.397 | 1.000 | 0.000 | 0/2 | 0/2 |
| **ALL** | **16** | **0.823** | **0.374** | **0.477** | **0.354** | **2/16** | **3/16** |

## Notable Observations

### `rb_02_speaker_fmv_ceiling` (rule_backed)
> What is the speaker FMV ceiling?
- groundedness_check.grounded=False — The retrieved content does not provide specific numeric thresholds for the FMV ceiling or mention the PhRMA Code's ceiling, making the claims ungrounded.

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- groundedness_check.grounded=False — The retrieved content supports the $75,000 cap for speaker fees but does not confirm it applies to total HCP compensation or compare it to the PhRMA Code.
- RAGAS Faithfulness=0.455 < 0.5

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The claims about lack of legitimate business purpose and high frequency of events are not directly supported by the retrieved content.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The answer makes specific claims about the absence of information on penalties, which are not directly supported by the retrieved content.

### `ret_03_phrma_sales_meal_conditions` (retrieval)
> Under the PhRMA Code, what conditions must be met for a sales representative to offer a meal to an HCP?
- groundedness_check.grounded=False — The specific meal limits attributed to the PhRMA Code are not supported by the retrieved content, which does not provide explicit conditions or numeric thresholds from the PhRMA Code.

### `ret_04_phrma_educational_items_threshold` (retrieval)
> Under the PhRMA Code, what is the value threshold for educational items that can be provided to HCPs, and what conditions apply?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about value thresholds or conditions for educational items under the PhRMA Code, nor does it mention Nova Pharma's policy on educational items.

### `ret_05_oig_compliance_program_elements` (retrieval)
> What are the seven elements of an effective pharmaceutical compliance program according to OIG guidance?
- groundedness_check.grounded=False — The retrieved content does not provide specific details about the seven elements of an effective pharmaceutical compliance program, making the claims ungrounded.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not specifically mention telehealth-only HCP interactions or provide direct guidance on this topic, making the claims ungrounded.
- RAGAS Faithfulness=0.111 < 0.5

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The answer makes several claims about the absence of specific rules and guidelines, but these claims are not directly supported by the retrieved content, which does not explicitly address Nova Pharma's rules on drug sample distribution.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The answer includes claims about state-specific policy segmentation and references to chunk IDs that are not supported by the retrieved content.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- safety net fired (prepended to data_limitations)

### `fp_03_quarterly_speaker_fee_limit` (false_premise)
> What is the quarterly speaker fee limit?
- groundedness_check.grounded=False — The retrieved content does not explicitly mention the absence of a quarterly speaker fee limit or clarify that the $3,500 limit applies per event.

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The retrieved content does not explicitly state that there is no annual meal-specific cap, nor does it confirm that the $75,000 cap includes all forms of compensation beyond speaker fees.

### `rg_02_office_visit_frequency_by_hcp_type` (registry_gap)
> What are Nova Pharma's office visit frequency limits per HCP per year, by HCP type?
- groundedness_check.grounded=False — The retrieved content does not provide information on office visit frequency limits, the specific meal frequency limit of 24 meals per HCP per year, or the annual cap on total HCP compensation of $75,000.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.719 | ✓ |
| answer_relevancy | ≥0.7 | 0.735 | ✓ |
| context_precision | ≥0.5 | 0.360 | ✗ |
| latency_p95_ms | ≤15000ms | 10674ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-27T07:40:44.446080+00:00  
**Current:**  2026-04-27T08:29:57.843120+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.807 → 0.719 (-0.088) | 0.710 → 0.735 (+0.025) | 0.369 → 0.360 (-0.009) | 0.667 → 0.542 (-0.125) |
| retrieval | 0.633 → 1.000 (+0.367) | 0.217 → 0.184 (-0.033) | 0.442 → 0.486 (+0.044) | 0.267 → 0.500 (+0.233) |
| unanswerable | 1.000 → 0.514 (-0.486) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) |
| false_premise | 0.604 → 0.811 (+0.207) | 0.430 → 0.442 (+0.012) | 0.425 → 0.588 (+0.162) | 0.500 → 0.333 (-0.167) |
| registry_gap | 0.906 → 1.000 (+0.094) | 0.324 → 0.397 (+0.074) | 1.000 → 1.000 (+0.000) | 0.333 → 0.000 (-0.333) |
| **ALL** | 0.770 → 0.823 (+0.052) | 0.366 → 0.374 (+0.008) | 0.435 → 0.477 (+0.042) | 0.385 → 0.354 (-0.031) |

