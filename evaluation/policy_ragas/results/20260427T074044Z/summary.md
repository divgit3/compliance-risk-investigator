# Policy RAGAS Baseline

**Generated:** 2026-04-27T07:40:44.446080+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.807 | 0.710 | 0.369 | 0.667 | 0/4 | 3/4 |
| retrieval | 5 | 0.633 | 0.217 | 0.442 | 0.267 | 0/5 | 0/5 |
| unanswerable | 2 | 1.000 | 0.000 | 0.000 | 0.000 | 0/2 | 0/2 |
| false_premise | 3 | 0.604 | 0.430 | 0.425 | 0.500 | 2/3 | 1/3 |
| registry_gap | 2 | 0.906 | 0.324 | 1.000 | 0.333 | 0/2 | 0/2 |
| **ALL** | **16** | **0.770** | **0.366** | **0.435** | **0.385** | **2/16** | **4/16** |

## Notable Observations

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- groundedness_check.grounded=False — The retrieved content supports the $75,000 cap on speaker fees but does not confirm it applies to total HCP compensation or that there are no additional PhRMA caps.

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not provide specific details or numeric thresholds related to the claims made in the answer about OIG fraud indicators or Nova Pharma's rules.

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about the criminal penalties for violating the federal anti-kickback statute, nor does it confirm the general statements about potential penalties.

### `ret_03_phrma_sales_meal_conditions` (retrieval)
> Under the PhRMA Code, what conditions must be met for a sales representative to offer a meal to an HCP?
- groundedness_check.grounded=False — The retrieved content does not provide specific meal value thresholds from the PhRMA Code, making the claim about PhRMA's meal limits ungrounded.

### `ret_04_phrma_educational_items_threshold` (retrieval)
> Under the PhRMA Code, what is the value threshold for educational items that can be provided to HCPs, and what conditions apply?
- groundedness_check.grounded=False — The retrieved content does not provide any specific value thresholds or guidelines regarding educational items for HCPs, making the claims ungrounded.

### `ret_05_oig_compliance_program_elements` (retrieval)
> What are the seven elements of an effective pharmaceutical compliance program according to OIG guidance?
- groundedness_check.grounded=False — The retrieved content mentions the existence of seven elements but does not list or describe them, so the specific factual claim about the elements is not grounded.
- RAGAS Faithfulness=0.000 < 0.5

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The answer makes specific claims about the absence of policy on telehealth-only HCP interactions, but the retrieved content does not provide any information on this topic to support these claims.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The retrieved content does not provide specific rules or guidance from Nova Pharma regarding drug sample distribution, nor does it confirm the claims made about OIG and PhRMA guidance.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about a per-specialty compensation cap for cardiologists or confirm the $75,000 annual cap for all HCPs, nor does it mention the PhRMA Code comparison.
- RAGAS Faithfulness=0.375 < 0.5
- safety net fired (prepended to data_limitations)

### `fp_03_quarterly_speaker_fee_limit` (false_premise)
> What is the quarterly speaker fee limit?
- groundedness_check.grounded=False — The claim about the PhRMA Code 2022 suggesting a $4,000 FMV ceiling is not supported by the retrieved content, as the relevant chunk does not mention this specific value.

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The retrieved content does not explicitly state that there is no annual meal-specific cap for HCPs, and the chunk ID DOC_002_chunk_0000 is not associated with the meal limits in the retrieved content.

### `rg_02_office_visit_frequency_by_hcp_type` (registry_gap)
> What are Nova Pharma's office visit frequency limits per HCP per year, by HCP type?
- groundedness_check.grounded=False — The retrieved content does not provide information on office visit frequency limits per HCP type or confirm the application of the 24 meals per HCP per year limit uniformly across all HCP types.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.807 | ✓ |
| answer_relevancy | ≥0.7 | 0.710 | ✓ |
| context_precision | ≥0.5 | 0.369 | ✗ |
| latency_p95_ms | ≤15000ms | 7934ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-26T11:50:18.805440+00:00  
**Current:**  2026-04-27T07:40:44.446080+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.658 → 0.807 (+0.149) | 0.735 → 0.710 (-0.025) | 0.235 → 0.369 (+0.134) | 0.667 → 0.667 (+0.000) |
| retrieval | 0.306 → 0.633 (+0.327) | 0.041 → 0.217 (+0.176) | 0.406 → 0.442 (+0.036) | 0.000 → 0.267 (+0.267) |
| unanswerable | 1.000 → 1.000 (+0.000) | 0.000 → 0.000 (+0.000) | 0.500 → 0.000 (-0.500) | 0.333 → 0.000 (-0.333) |
| false_premise | 0.628 → 0.604 (-0.024) | 0.482 → 0.430 (-0.052) | 0.303 → 0.425 (+0.122) | 0.333 → 0.500 (+0.167) |
| registry_gap | 0.875 → 0.906 (+0.031) | 0.356 → 0.324 (-0.033) | 0.000 → 1.000 (+1.000) | 0.000 → 0.333 (+0.333) |
| **ALL** | 0.612 → 0.770 (+0.158) | 0.332 → 0.366 (+0.035) | 0.305 → 0.435 (+0.130) | 0.271 → 0.385 (+0.115) |

