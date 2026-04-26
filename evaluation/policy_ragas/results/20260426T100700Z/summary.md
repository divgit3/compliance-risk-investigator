# Policy RAGAS Baseline

**Generated:** 2026-04-26T10:07:00.788276+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.668 | 0.735 | 0.244 | 0.542 | 0/4 | 3/4 |
| retrieval | 5 | 0.299 | 0.041 | 0.406 | 0.000 | 1/5 | 0/5 |
| unanswerable | 2 | 0.250 | 0.000 | 0.000 | 0.000 | 0/2 | 0/2 |
| false_premise | 3 | 0.572 | 0.486 | 0.303 | 0.417 | 2/3 | 0/3 |
| registry_gap | 2 | 0.938 | 0.397 | 0.000 | 0.000 | 0/2 | 0/2 |
| **ALL** | **16** | **0.516** | **0.337** | **0.245** | **0.213** | **3/16** | **3/16** |

## Notable Observations

### `rb_03_annual_compensation_cap` (rule_backed)
> What is the annual cap on total HCP compensation?
- groundedness_check.grounded=False — The retrieved content does not provide a specific chunk ID or document source that matches the claim about the $75,000 cap or the specific chunk ID mentioned in the answer.
- RAGAS Faithfulness=0.167 < 0.5

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The generated answer does not contain any specific factual claims or details that can be evaluated for grounding against the retrieved content.
- RAGAS Faithfulness=0.000 < 0.5

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The generated answer makes specific claims about the absence of information and rules regarding the federal anti-kickback statute, which are not supported by the retrieved content.
- RAGAS Faithfulness=0.000 < 0.5

### `ret_03_phrma_sales_meal_conditions` (retrieval)
> Under the PhRMA Code, what conditions must be met for a sales representative to offer a meal to an HCP?
- groundedness_check.grounded=False — The retrieved content does not provide specific conditions from the PhRMA Code regarding offering meals to HCPs, nor does it mention the entity_scope or source document cited in the answer.
- safety net fired (prepended to data_limitations)

### `ret_04_phrma_educational_items_threshold` (retrieval)
> Under the PhRMA Code, what is the value threshold for educational items that can be provided to HCPs, and what conditions apply?
- groundedness_check.grounded=False — The retrieved content does not provide any information about value thresholds for educational items, meal limits, or compensation caps, making these claims ungrounded.

### `ret_05_oig_compliance_program_elements` (retrieval)
> What are the seven elements of an effective pharmaceutical compliance program according to OIG guidance?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about the seven elements of an effective pharmaceutical compliance program according to OIG guidance, making the claims ungrounded.
- RAGAS Faithfulness=0.050 < 0.5

### `un_01_telehealth_specific_policy` (unanswerable)
> What is Nova Pharma's policy on telehealth-only HCP interactions?
- groundedness_check.grounded=False — The retrieved content does not provide any information about Nova Pharma's policy on telehealth-only HCP interactions or confirm the uniform application of meal limits and compensation caps to telehealth interactions.

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The answer makes specific claims about the absence of guidance and rules, which are not supported by the retrieved content as no relevant information on drug sample distribution was found in the provided excerpts.
- RAGAS Faithfulness=0.000 < 0.5

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The answer includes claims about state-specific policy segmentation and specific chunk IDs that are not supported by the retrieved content.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about a $75,000 annual compensation cap, nor does it mention a comparison with the PhRMA Code or a specific rule ID COMP_001 from DOC_002_chunk_0001.
- RAGAS Faithfulness=0.200 < 0.5
- safety net fired (prepended to data_limitations)

### `fp_03_quarterly_speaker_fee_limit` (false_premise)
> What is the quarterly speaker fee limit?
- groundedness_check.grounded=False — The retrieved content does not provide information about a quarterly speaker fee limit or a $4,000 limit in the PhRMA Code, making these claims ungrounded.

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The claim about the annual HCP compensation cap and the specific document references are not supported by the retrieved content.

### `rg_02_office_visit_frequency_by_hcp_type` (registry_gap)
> What are Nova Pharma's office visit frequency limits per HCP per year, by HCP type?
- groundedness_check.grounded=False — The retrieved content does not provide information about office visit frequency limits or segmentation by HCP type, making these claims ungrounded.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.668 | ✗ |
| answer_relevancy | ≥0.7 | 0.735 | ✓ |
| context_precision | ≥0.5 | 0.244 | ✗ |
| latency_p95_ms | ≤15000ms | 11024ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-25T18:53:26.650195+00:00  
**Current:**  2026-04-26T10:07:00.788276+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.000 → 0.668 (+0.668) | N/A → 0.735 | 0.000 → 0.244 (+0.244) | 0.000 → 0.542 (+0.542) |
| retrieval | 0.243 → 0.299 (+0.056) | N/A → 0.041 | 0.000 → 0.406 (+0.406) | 0.000 → 0.000 (+0.000) |
| unanswerable | 0.625 → 0.250 (-0.375) | N/A → 0.000 | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) |
| false_premise | 0.061 → 0.572 (+0.512) | N/A → 0.486 | 0.000 → 0.303 (+0.303) | 0.000 → 0.417 (+0.417) |
| registry_gap | 0.385 → 0.938 (+0.553) | N/A → 0.397 | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) |
| **ALL** | 0.211 → 0.516 (+0.305) | N/A → 0.337 | 0.000 → 0.245 (+0.245) | 0.000 → 0.213 (+0.213) |

