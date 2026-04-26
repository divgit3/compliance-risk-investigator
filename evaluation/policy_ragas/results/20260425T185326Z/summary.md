# Policy RAGAS Baseline

**Generated:** 2026-04-25T18:53:26.650195+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.000 | N/A | 0.000 | 0.000 | 0/4 | 3/4 |
| retrieval | 5 | 0.243 | N/A | 0.000 | 0.000 | 1/5 | 0/5 |
| unanswerable | 2 | 0.625 | N/A | 0.000 | 0.000 | 0/2 | 0/2 |
| false_premise | 3 | 0.061 | N/A | 0.000 | 0.000 | 2/3 | 0/3 |
| registry_gap | 2 | 0.385 | N/A | 0.000 | 0.000 | 0/2 | 0/2 |
| **ALL** | **16** | **0.211** | **N/A** | **0.000** | **0.000** | **3/16** | **3/16** |

## Notable Observations

### `rb_01_lunch_meal_limit` (rule_backed)
> What is the meal limit for lunch?
- RAGAS Faithfulness=0.000 < 0.5

### `rb_02_speaker_fmv_ceiling` (rule_backed)
> What is the speaker FMV ceiling?
- RAGAS Faithfulness=0.000 < 0.5

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- groundedness_check.grounded=False — The retrieved content does not contain any information about an annual cap of $75,000 or a rule ID COMP_001, making these claims ungrounded.
- RAGAS Faithfulness=0.000 < 0.5

### `rb_04_meal_per_attendee_ceiling` (rule_backed)
> What is the per-attendee meal cost ceiling at speaker events?
- RAGAS Faithfulness=0.000 < 0.5

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The generated answer does not contain any specific factual claims to evaluate for grounding, as it was stopped due to max iterations.
- RAGAS Faithfulness=0.000 < 0.5

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The answer makes a specific claim about the absence of information on criminal penalties for the federal anti-kickback statute, which is not supported by the retrieved content.
- RAGAS Faithfulness=0.000 < 0.5

### `ret_03_phrma_sales_meal_conditions` (retrieval)
> Under the PhRMA Code, what conditions must be met for a sales representative to offer a meal to an HCP?
- groundedness_check.grounded=False — The retrieved content does not provide specific conditions from the PhRMA Code or confirm the applicability scope of the meal limits as per the generated answer.
- RAGAS Faithfulness=0.222 < 0.5
- safety net fired (prepended to data_limitations)

### `ret_04_phrma_educational_items_threshold` (retrieval)
> Under the PhRMA Code, what is the value threshold for educational items that can be provided to HCPs, and what conditions apply?
- groundedness_check.grounded=False — The answer makes claims about the absence of specific thresholds for educational items, but no retrieved content supports these claims.

### `ret_05_oig_compliance_program_elements` (retrieval)
> What are the seven elements of an effective pharmaceutical compliance program according to OIG guidance?
- groundedness_check.grounded=False — The retrieved content does not provide any information about the seven elements of an effective pharmaceutical compliance program according to OIG guidance, making the claims ungrounded.

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not contain any specific information or rules regarding telehealth-only HCP interactions, nor does it confirm the absence of such policies or segmentation by HCP specialty or role.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The retrieved content does not provide specific guidance or rules on distributing drug samples to HCPs, nor does it confirm the absence of such rules in the registry.
- RAGAS Faithfulness=0.250 < 0.5

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The answer incorrectly references a Chunk ID (DOC_002_chunk_0000) that does not appear in the retrieved content, making the claim ungrounded.
- RAGAS Faithfulness=0.000 < 0.5
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about a $75,000 annual compensation cap, nor does it mention Nova Pharma's policy being stricter than the PhRMA Code regarding compensation caps.
- RAGAS Faithfulness=0.000 < 0.5
- safety net fired (prepended to data_limitations)

### `fp_03_quarterly_speaker_fee_limit` (false_premise)
> What is the quarterly speaker fee limit?
- groundedness_check.grounded=False — The claim about the PhRMA Speaker Fee Limit being $4,000 per engagement is not supported by the retrieved content, as it is not explicitly mentioned in the retrieved policy chunks or rules.
- RAGAS Faithfulness=0.182 < 0.5

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The answer includes specific chunk IDs that do not appear in the retrieved content, making those claims ungrounded.
- RAGAS Faithfulness=0.000 < 0.5

### `rg_02_office_visit_frequency_by_hcp_type` (registry_gap)
> What are Nova Pharma's office visit frequency limits per HCP per year, by HCP type?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about office visit frequency limits per HCP per year or confirm the application of the 'Max Meals Per HCP Per Year' rule to office visits.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.000 | ✗ |
| answer_relevancy | ≥0.7 | N/A | ✗ |
| context_precision | ≥0.5 | 0.000 | ✗ |
| latency_p95_ms | ≤15000ms | 7162ms | ✓ |
