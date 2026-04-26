# Policy RAGAS Baseline

**Generated:** 2026-04-26T11:50:18.805440+00:00  
**Total entries:** 16

## Per-Category Metric Summary

| Category | N | Faithful. | Ans.Rel. | Ctx.Prec. | Ctx.Rec. | Safety Net | Grounded |
|----------|---|-----------|----------|-----------|----------|------------|----------|
| rule_backed | 4 | 0.658 | 0.735 | 0.235 | 0.667 | 0/4 | 4/4 |
| retrieval | 5 | 0.306 | 0.041 | 0.406 | 0.000 | 0/5 | 1/5 |
| unanswerable | 2 | 1.000 | 0.000 | 0.500 | 0.333 | 0/2 | 1/2 |
| false_premise | 3 | 0.628 | 0.482 | 0.303 | 0.333 | 2/3 | 1/3 |
| registry_gap | 2 | 0.875 | 0.356 | 0.000 | 0.000 | 0/2 | 0/2 |
| **ALL** | **16** | **0.612** | **0.332** | **0.305** | **0.271** | **2/16** | **7/16** |

## Notable Observations

### `ret_01_oig_speaker_fraud_indicators` (retrieval)
> What characteristics has the OIG identified as potentially indicating a speaker program arrangement that could violate the anti-kickback statute?
- groundedness_check.grounded=False — The generated answer does not contain any specific factual claims to evaluate for grounding, as the agent stopped due to max iterations without providing a complete answer.
- RAGAS Faithfulness=0.000 < 0.5

### `ret_02_anti_kickback_penalties` (retrieval)
> What are the criminal penalties for violating the federal anti-kickback statute?
- groundedness_check.grounded=False — The answer makes claims about the absence of information and rules, which cannot be verified as the retrieved content does not address the federal anti-kickback statute or its penalties.
- RAGAS Faithfulness=0.000 < 0.5

### `ret_03_phrma_sales_meal_conditions` (retrieval)
> Under the PhRMA Code, what conditions must be met for a sales representative to offer a meal to an HCP?
- groundedness_check.grounded=False — The answer includes claims about the absence of specific conditions in the PhRMA Code and references to chunk IDs that are not present in the retrieved content.

### `ret_04_phrma_educational_items_threshold` (retrieval)
> Under the PhRMA Code, what is the value threshold for educational items that can be provided to HCPs, and what conditions apply?
- groundedness_check.grounded=False — The answer claims that the PhRMA Code does not define a specific value threshold for educational items, but there is no retrieved content to support or refute this claim.

### `ret_05_oig_compliance_program_elements` (retrieval)
> What are the seven elements of an effective pharmaceutical compliance program according to OIG guidance?
- RAGAS Faithfulness=0.000 < 0.5

### `un_02_drug_sample_distribution_rules` (unanswerable)
> What are Nova Pharma's specific rules for distributing drug samples to HCPs?
- groundedness_check.grounded=False — The answer makes specific claims about the absence of information on drug sample distribution rules, but the retrieved content does not provide evidence to support these claims.

### `fp_01_california_meal_limit` (false_premise)
> What is the meal limit for HCPs in California specifically?
- groundedness_check.grounded=False — The retrieved content does not provide information about state-specific meal limits or the specific chunk IDs mentioned in the answer.
- safety net fired (prepended to data_limitations)

### `fp_02_per_specialty_compensation_cap` (false_premise)
> What is the per-specialty compensation cap for cardiologists?
- groundedness_check.grounded=False — The retrieved content does not provide specific information about a $75,000 annual compensation cap or a comparison between Nova Pharma's approach and the PhRMA Code.
- RAGAS Faithfulness=0.385 < 0.5
- safety net fired (prepended to data_limitations)

### `rg_01_annual_meal_cap` (registry_gap)
> What is Nova Pharma's annual meal cap for HCPs?
- groundedness_check.grounded=False — The claim about the annual cap on total HCP compensation being $75,000 is not supported by the retrieved content, as there is no chunk with chunk_id: DOC_002_chunk_0001 provided.

### `rg_02_office_visit_frequency_by_hcp_type` (registry_gap)
> What are Nova Pharma's office visit frequency limits per HCP per year, by HCP type?
- groundedness_check.grounded=False — The retrieved content does not provide information about office visit frequency limits or segmentation by HCP type, nor does it confirm the uniform application of interaction frequency rules across all HCPs.

## CI Gates (rule_backed category only)

| Gate | Threshold | rule_backed mean | Pass? |
|------|-----------|------------------|-------|
| faithfulness | ≥0.7 | 0.658 | ✗ |
| answer_relevancy | ≥0.7 | 0.735 | ✓ |
| context_precision | ≥0.5 | 0.235 | ✗ |
| latency_p95_ms | ≤15000ms | 8528ms | ✓ |

## Delta from Previous Baseline

**Previous:** 2026-04-26T10:07:00.788276+00:00  
**Current:**  2026-04-26T11:50:18.805440+00:00

| Category | Faithfulness | Ans.Rel. | Ctx.Prec. | Ctx.Rec. |
|----------|-------------|----------|-----------|----------|
| rule_backed | 0.668 → 0.658 (-0.010) | 0.735 → 0.735 (+0.000) | 0.244 → 0.235 (-0.009) | 0.542 → 0.667 (+0.125) |
| retrieval | 0.299 → 0.306 (+0.007) | 0.041 → 0.041 (+0.000) | 0.406 → 0.406 (+0.000) | 0.000 → 0.000 (+0.000) |
| unanswerable | 0.250 → 1.000 (+0.750) | 0.000 → 0.000 (+0.000) | 0.000 → 0.500 (+0.500) | 0.000 → 0.333 (+0.333) |
| false_premise | 0.572 → 0.628 (+0.056) | 0.486 → 0.482 (-0.004) | 0.303 → 0.303 (+0.000) | 0.417 → 0.333 (-0.083) |
| registry_gap | 0.938 → 0.875 (-0.062) | 0.397 → 0.356 (-0.041) | 0.000 → 0.000 (+0.000) | 0.000 → 0.000 (+0.000) |
| **ALL** | 0.516 → 0.612 (+0.096) | 0.337 → 0.332 (-0.006) | 0.245 → 0.305 (+0.060) | 0.213 → 0.271 (+0.057) |

