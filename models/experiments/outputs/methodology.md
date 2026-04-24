# Compliance Risk Investigator — Model Selection and Methodology

## Overview

This document records the model selection rationale for the Compliance Risk Investigator project, the evaluation methodology used to compare candidate detectors, and the final deployment recommendation. It is written for a reader who has seen the feature engineering and model-fitting code but has not yet seen how the models perform relative to each other or to ground truth.

The project detects HCPs whose interaction patterns with pharmaceutical sales reps are consistent with compliance violations under the PhRMA Code, OIG Guidance, Anti-Kickback Statute, and Sunshine Act frameworks. Three detection methods were selected for production evaluation: a deterministic rules engine, Isolation Forest (IF), and COPOD. This document explains why these three and not others, how they were evaluated against a layered ground truth, and what the comparison reveals about detection in this domain.

## Problem Framing

Compliance detection sits in a category of problems where labels are scarce by construction. At deployment time, a compliance team does not know which HCPs are violators — if it did, the detection problem would be solved by lookup. The purpose of the detector is to surface candidates for investigator review, and the investigator produces the labels after the fact. This rules out supervised learning at the outset of any new deployment, and the project is designed as an unsupervised detection system with supervised methods reserved for a Phase 2 refinement once labels have accumulated.

The evaluation problem is therefore slightly different from the production problem. Because the underlying data is synthetic, we have access to labels that would not exist in a real deployment. Using these labels correctly — distinguishing between labels that measure the detector's target and labels that measure the detector's own output — turned out to be the single most consequential methodological decision in the project.

## Models Considered

### Selected

Three models were selected to span the space of unsupervised detection strategies.

The **rules engine** encodes known compliance violations — speaker fees exceeding fair market value, annual compensation caps exceeded, same-office attendee-speaker patterns, and similar — as 23 deterministic flag conditions operating on aggregated HCP features. Each flag carries a policy citation. Its output, `rule_score`, is the count-weighted sum of triggered flags per HCP. This is the baseline: any anomaly detector needs to at least match what explicit rules already catch, and needs a justification for any HCPs it surfaces that rules do not.

**Isolation Forest** was chosen as the primary statistical anomaly detector. It is tree-based, fast at inference, scales well to the 97,011 HCP × 99 feature matrix, and supports SHAP-based per-HCP explanation. Its inductive bias — points isolable in few splits are anomalous — suits a feature space where outliers are expected to be sparse and high-dimensional.

**COPOD** was chosen as a second statistical anomaly detector with different inductive assumptions. Where IF is a tree ensemble with a global contamination parameter, COPOD is parametric: it fits an empirical copula per feature and scores each HCP by the tail probability of its observed feature values. This provides two benefits. First, it is a cross-check on IF — if two methods with different assumptions agree about which HCPs are unusual, the signal is more likely to reflect structure in the data rather than algorithm artifact. Second, COPOD's output is natively interpretable per feature: each HCP's anomaly score decomposes into "how extreme is this HCP on each dimension independently," which complements SHAP's game-theoretic attribution.

### Rejected

**Local Outlier Factor** relies on local density estimation. At 97,011 × 99 this requires either a full distance matrix (infeasible) or k-NN approximations that introduce tuning burden without obvious benefit over tree-based isolation. IF's inductive bias matches this dataset better.

**One-Class SVM** has a kernel and gamma tuning surface that is hard to defend in a deployment setting, and its inference cost scales poorly. COPOD already fills the "second independent method" role with less methodological fragility.

**Autoencoders** introduce training complexity (architecture, regularization, reconstruction loss) for gains that have not been consistently demonstrated on tabular data of this size. For 99 features and under 100k rows, the evidence favors tree and parametric methods.

**Supervised classifiers** (logistic regression, gradient-boosted trees) were considered and deferred. These methods require labeled violation data, which by definition does not exist at deployment time in a novel compliance context — if investigators already knew which HCPs were violators, the detection problem would be solved. Supervised methods are appropriate as a Phase 2 enhancement once a compliance team has accumulated investigator-confirmed outcomes from an initial period of unsupervised detection. At that point, a supervised model could be trained on the residual signal not captured by rules or anomaly detection.

## Methodology

### Feature Set

99 features were engineered from the underlying HCP-rep interaction records, spanning five groups: spend magnitude (`peak_year_spend`, `spend_2024_raw`, `pct_food_beverage`), peer-relative anomaly indicators (`np_spend_vs_peer_avg_2024`, `np_spend_outlier_2022_real`), rank and count features (`np_escalating_rank`, `np_outlier_years_count`, `sow_dominant_years_count`), interaction network features (`unique_reps_interacted`, `interaction_frequency_score`, `total_interactions`), and policy compliance state (`annual_cap_pct_used_2022`). The same matrix was used to fit IF and COPOD; the rules engine consumes the same features through its own flag logic.

### Label Taxonomy

The synthetic data generator produces three distinct sources of labels, and understanding the relationship between them is essential to interpreting the results.

At the lowest layer is `hcp_violation_profile`, a latent categorical attribute assigned to each HCP at generation time with four values: `clean` (69.8%), `minor` (20.2%), `moderate` (6.0%), and `serious` (3.9%). This profile governs the probabilistic emission of the HCP's interaction records — serious HCPs are more likely to generate over-FMV speaker fees, moderate HCPs more likely to exceed annual compensation caps, and so on.

The middle layer is the emitted records themselves.

The top layer is two independent re-evaluations of those records against the PhRMA/OIG rulebook. `apply_violation_flags()` in the synthetic generator produces record-level violation flags that aggregate to `has_violation` and `max_severity`. `rule_based_flags.py` in the feature pipeline produces HCP-level rule flags that aggregate to `rule_score`. These two implementations share no code — they were written independently at different stages of the pipeline — but they encode the same compliance rulebook. Consequently they agree with each other roughly 99% of the time on which HCPs are violators. This is calibration, not evaluation: measuring rules against `max_severity` is largely measuring whether two implementations of the same rulebook agree.

The only label that is not downstream of the rulebook is `hcp_violation_profile`. It is therefore the primary evaluation target for this project. `has_violation` and `is_high` (derived from `max_severity == 'high'`) are retained in the comparison as calibration checks — their purpose is to surface the structural inflation that arises when evaluating rules against rule-adjacent labels, and to make that inflation visible and documented rather than hidden.

### Metrics

Each model produces a continuous score per HCP. To make the three scores comparable and operationally meaningful, we evaluate at fixed top-K thresholds (top 1%, 5%, 10%) rather than at score-specific cutoffs. For the rules engine we additionally evaluate at its natural operating threshold — any HCP with `rule_score > 0`, which is 14.4% of the population — because this is how rules would be deployed in practice.

At each threshold we report precision (fraction of flagged HCPs that are true positives), recall (fraction of true positives captured), F1, and lift (precision divided by base rate). Lift is the most interpretable metric at low base rates: a lift of 2.0 means the detector flags HCPs who are twice as likely to be positive as a random draw. AUC-ROC is not reported because at base rates of 3.9% (serious profile) and 0.39% (high severity), the ROC curve is dominated by the large negative class and becomes uninformative.

## Results

### Primary Evaluation: `hcp_violation_profile == 'serious'`

This is the evaluation that measures whether each detector recovers the latent intent of the synthetic violator, independent of the rulebook. Base rate: 3.93%.

| Model | Threshold | Flagged | TP | Precision | Recall | Lift |
|---|---|---|---|---|---|---|
| anomaly_score | top 1% | 971 | 38 | 3.9% | 1.0% | 1.00 |
| anomaly_score | top 5% | 4,851 | 206 | 4.2% | 5.4% | 1.08 |
| anomaly_score | top 10% | 9,702 | 403 | 4.2% | 10.6% | 1.06 |
| copod_score | top 1% | 971 | 37 | 3.8% | 1.0% | 0.97 |
| copod_score | top 5% | 4,851 | 204 | 4.2% | 5.4% | 1.07 |
| copod_score | top 10% | 9,702 | 414 | 4.3% | 10.9% | 1.09 |
| rule_score | top 1% | 971 | 80 | 8.2% | 2.1% | 2.10 |
| rule_score | top 5% | 4,851 | 239 | 4.9% | 6.3% | 1.25 |
| rule_score | top 10% | 9,702 | 443 | 4.6% | 11.6% | 1.16 |
| rule_score | natural (14.4%) | 13,974 | 629 | 4.5% | 16.5% | 1.15 |

The primary finding of this project is a negative result: no detector strongly recovers the latent violator profile. Rules-only achieves the highest lift (2.1× at top 1%), meaning HCPs in its top-1% slice are twice as likely to be serious violators as random HCPs. But at its natural operating threshold of 14.4% flagged, rules captures only 16.5% of serious violators. IF and COPOD hover at lift ≈ 1 across all thresholds — they are essentially indistinguishable from random sampling for this target.

This is consistent with the provenance of the serious label. The generator assigns intent at the HCP level, then emits records probabilistically. By the time records are aggregated into features, the signal attenuates substantially. Some serious HCPs generate obvious rule-triggering records; many do not. Statistical outlier detection, which is not optimized for this specific generative process, cannot meaningfully recover it.

The honest interpretation for production: unsupervised detection in this domain is a partial-recovery problem, not a solved problem. A flag should be treated as an increase in investigation priority, not as evidence of violation. Absence of a flag is not evidence of compliance — 83.5% of serious-profile HCPs carry no rule flag at the natural threshold.

### Robustness: `hcp_violation_profile in {'serious', 'moderate'}`

Base rate: 9.97%.

| Model | Threshold | Flagged | TP | Precision | Recall | Lift |
|---|---|---|---|---|---|---|
| anomaly_score | top 1% | 971 | 106 | 10.9% | 1.1% | 1.10 |
| anomaly_score | top 5% | 4,851 | 494 | 10.2% | 5.1% | 1.02 |
| anomaly_score | top 10% | 9,702 | 969 | 10.0% | 10.0% | 1.00 |
| copod_score | top 1% | 971 | 93 | 9.6% | 1.0% | 0.96 |
| copod_score | top 5% | 4,851 | 494 | 10.2% | 5.1% | 1.02 |
| copod_score | top 10% | 9,702 | 988 | 10.2% | 10.2% | 1.02 |
| rule_score | top 1% | 971 | 175 | 18.0% | 1.8% | 1.81 |
| rule_score | top 5% | 4,851 | 590 | 12.2% | 6.1% | 1.22 |
| rule_score | top 10% | 9,702 | 1,073 | 11.1% | 11.1% | 1.11 |
| rule_score | natural (14.4%) | 13,974 | 1,517 | 10.9% | 15.7% | 1.09 |

Broadening the target from `serious` only (3.9% base rate) to
`serious ∪ moderate` (9.97%) narrows the rules-vs-outlier-methods gap
rather than widening it. Rules top-1% lift drops from 2.10× to 1.81×,
while IF and COPOD remain near 1.0× across all thresholds. This is
consistent with rules being calibrated to flag the most severe
behavior — adding `moderate` HCPs to the positive class pulls in
cases whose records do not cleanly trigger high-severity flags, so
rules' precision advantage dilutes. IF and COPOD do not improve,
confirming that neither method detects the latent violator profile
at any severity tier; they detect statistical extremity, which is a
different property.

### Calibration Checks

Against `is_high` (base rate 0.39%), rules-only achieves 84.8% recall at top 1% with a lift of 84.7×. IF and COPOD sit at ≤ 1% recall and lifts near 1. This enormous gap is the documented structural-inflation effect: `is_high` and `rule_score` are two implementations of the same rulebook, and the comparison measures their agreement rather than the rules engine's detection quality. This is noted here not as a model-selection input but as an explicit demonstration of the methodological trap that gives this project its interpretive spine.

Against `has_violation` (base rate 37.13%), a similar but weaker version of the same effect appears. Rules-only achieves 95.3% precision at top 1%; IF and COPOD sit at the base rate. The precision number is real but its meaning is circumscribed — it says that rule-flagged HCPs almost always have at least one violation record, which follows from the shared rulebook.

### SHAP Decomposition

A secondary finding emerges from the SHAP analysis of IF. Across the full population, the features driving IF's anomaly score are dominated by rank and count features: `np_escalating_rank` contributes 4.6% of total importance, followed by peer-outlier counts and `sow_dominant_years_count`. Among the top 1% of HCPs flagged by IF, the ranking shifts sharply toward magnitude features: `np_spend_vs_peer_avg_2024`, `pct_food_beverage`, `peak_year_spend`, `spend_2024_raw`, with `unique_reps_interacted` and interaction frequency following.

The interpretation: rank features separate the bulk of the population from a diffuse middle, but magnitude features separate the tail from everyone else. What makes an HCP globally "unusual" is not the same as what makes an HCP extremely unusual. This has a practical consequence for explanation — SHAP values computed at the top of the anomaly distribution emphasize different features than global feature importance would suggest, and investigators should be given the per-HCP SHAP rather than the global ranking when reviewing flagged cases.

## Example Records

Three HCPs are presented below to illustrate the three explanation mechanisms in combination: rule flags (what known pattern, if any, did this HCP trigger), SHAP values (which features drove the IF anomaly score for this HCP), and COPOD per-feature tail percentiles (on which features does this HCP sit in the extreme tail of the population).

### Case A: Rules and IF agree

**HCP A-0001** — rule_score: 100.0 (capped) · anomaly_score: 89.59 (99.85th percentile) · copod_score: 221.13

Fired rule flags (12):

| Flag | Severity |
|---|---|
| flag_meal_limit_breach | medium |
| flag_meal_chronic_breach | high |
| flag_speaker_fmv_breach | high |
| flag_speaker_fmv_chronic | critical |
| flag_repeat_speaker | medium |
| flag_high_repeat_speaker | high |
| flag_rapid_repeat_pattern | medium |
| flag_missing_attestation | medium |
| flag_vague_rationale | medium |
| flag_fmv_non_compliance | high |
| flag_speaking_fee_concentration | high |
| flag_escalating_rank | medium |

Top-5 SHAP features (|value|, descending):

| Feature | |SHAP| |
|---|---|
| np_spend_vs_peer_avg_2023_real | 0.5268 |
| np_spend_vs_peer_avg_2024 | 0.5031 |
| unique_reps_interacted | 0.4844 |
| sow_dominant_years_count | 0.4452 |
| spend_2024_raw | 0.4345 |

Top-5 COPOD tail probabilities (smallest = most extreme):

| Feature | Tail prob |
|---|---|
| sow_dominant_years_count | 0.00004 |
| np_spend_vs_peer_avg_2024 | 0.00036 |
| np_spend_vs_peer_avg_2024_real | 0.00036 |
| unique_reps_interacted | 0.00065 |
| interaction_frequency_score | 0.00065 |

Rules, SHAP, and COPOD converge on the same story: this HCP is at the extreme tail on peer-relative spend and rep network width, and the rule engine caught it on every dimension it checks — FMV breaches at both threshold and chronic level, meal policy, attestation, and escalating engagement rank. The COPOD tail probability of 0.00004 on `sow_dominant_years_count` means this HCP's share-of-wallet concentration is in the top 0.004% of the population; the rules catch the FMV breach, but the unusually narrow rep concentration is a structural feature the rules do not encode. This is the strongest-signal case in the dataset: all three mechanisms agree and reinforce each other.

### Case B: IF flags without rule firing

**HCP B-0001** — rule_score: 0.0 (no flags fired) · anomaly_score: 66.36 (99.20th percentile) · copod_score: 196.40

No rule flags fired.

Top-5 SHAP features (|value|, descending):

| Feature | |SHAP| | Note |
|---|---|---|
| np_spend_vs_peer_avg_2024 | 0.7379 | magnitude |
| pct_food_beverage | 0.6578 | magnitude |
| sow_dominant_years_count | 0.5741 | |
| yoy_growth_2324 | 0.4235 | |
| spend_2024_raw | 0.3901 | magnitude |

Top-5 COPOD tail probabilities (smallest = most extreme):

| Feature | Tail prob |
|---|---|
| raw_event_risk_score_max | 0.00001 |
| total_program_cost_mean | 0.00001 |
| total_program_cost_max | 0.00001 |
| total_program_cost_sum | 0.00001 |
| meal_cost_per_attendee_mean | 0.00002 |

Three of the top-5 SHAP features are magnitude signals (peer-relative spend, food-and-beverage concentration, raw spend), yet none triggered a rule flag — the HCP's absolute spend values did not individually cross the per-meal or annual-cap thresholds the rules check. The COPOD output tells a different story: this HCP is in the extreme tail on program cost metrics (mean, max, and total program cost all at the 0.001st percentile), with meal-per-attendee cost also near zero tail probability. This is precisely the novel-pattern case: statistically extraordinary on program economics in a way that the current 23 rules do not capture. The gap worth investigating is whether a composite program-cost flag — triggering when both `total_program_cost_mean` and `meal_cost_per_attendee_mean` are simultaneously in the 99th percentile — would surface this pattern at lower false-positive cost than the current IF ranking.

### Case C: Rules flag without IF firing

**HCP C-0001** — rule_score: 100.0 (capped) · anomaly_score: 7.14 (52.85th percentile) · copod_score: 25.24

Fired rule flags (12), including 2 FMV-related flags meeting the policy-threshold criteria:

| Flag | Severity |
|---|---|
| flag_meal_limit_breach | medium |
| flag_meal_chronic_breach | high |
| flag_speaker_fmv_breach | high |
| flag_speaker_fmv_chronic | critical |
| flag_repeat_speaker | medium |
| flag_high_repeat_speaker | high |
| flag_rapid_repeat_pattern | medium |
| flag_missing_attestation | medium |
| flag_chronic_missing_attestation | high |
| flag_vague_rationale | medium |
| flag_fmv_non_compliance | high |
| flag_speaking_fee_concentration | high |

Top-5 SHAP features (|value|, descending):

| Feature | |SHAP| |
|---|---|
| sow_dominant_years_count | 0.4572 |
| engagement_priority_score | 0.3658 |
| np_spend_outlier_2023_real | 0.0708 |
| np_escalating_rank | 0.0693 |
| np_spend_pct_rank_specialty_2024_real | 0.0622 |

Top-5 COPOD tail probabilities (smallest = most extreme):

| Feature | Tail prob |
|---|---|
| engagement_priority_score | 0.02509 |
| sow_dominant_years_count | 0.03190 |
| spend_2023 | 0.28742 |
| annual_cap_pct_used_2023 | 0.28742 |
| np_spend_pct_rank_specialty_2023 | 0.28742 |

This HCP sits at the 52.85th percentile of IF anomaly score — the middle of the population — while carrying a rule_score capped at 100 with 12 fired flags including critical FMV violations. The SHAP and COPOD outputs explain why: the top SHAP features are engagement and share-of-wallet indicators, not the magnitude and spend-outlier signals that drive the IF tail. The most extreme COPOD tail probabilities bottom out at 0.025, not the sub-0.001 values seen in Cases A and B. The HCP crossed specific policy thresholds (FMV ceiling, attestation requirements, repeat-speaker limits) that trigger deterministic rule firing, but its overall feature profile — the combination of 99 dimensions that IF evaluates holistically — is unremarkable. The contrast with Case A is instructive: both HCPs fire 12 flags including FMV violations, but Case A's SHAP values on spend magnitude features are substantially higher (`np_spend_vs_peer_avg_2024`: 0.50, `spend_2024_raw`: 0.43 vs. neither appearing in Case C's top 5), while Case C's drivers shift toward engagement and share-of-wallet indicators — meaning Case C's HCP crossed policy thresholds without producing anomalous spend magnitudes. This demonstrates the core complementarity: rules catch known threshold violations regardless of statistical context; IF catches statistical outliers regardless of which thresholds were crossed.

Taken together, the three cases illustrate why the ensemble is structured as it is. Case A is the canonical highest-priority investigation: rules, SHAP, and COPOD all converge on the same HCP with mutually reinforcing explanations, leaving little ambiguity about where investigator attention should go first. Case B is the case that justifies including IF and COPOD in the ensemble at all — a rule_score of zero would cause a rules-only deployment to never surface this HCP, despite its program cost metrics sitting at the 0.001st percentile; the novel-pattern signal exists only because two independent anomaly detectors with different assumptions both flag it. Case C is the policy-enforcement case where the ensemble adds nothing beyond rules, and where rules remain essential precisely because the violation is about a specific threshold being crossed, not about statistical unusualness. An investigator queue that treated all flagged HCPs as equivalent — or worse, ranked them by a single score — would miss both the novel patterns that Case B represents and the appropriate elevation of Case A's triple-agreement signal above the much larger pool of Case C-type single-mechanism flags.

## Final Model Selection

The results admit two defensible selection strategies depending on
deployment constraints. Both are recorded here. The project takes the
ensemble forward as the primary recommendation; the single-model option
is preserved as a documented fallback for deployments where ensemble
operation is not feasible.

### Recommended: Three-Model Ensemble

Rules + Isolation Forest + COPOD, deployed as a tiered screen.

The three detectors answer different questions and the results show they
are not redundant. Rules achieves the highest lift on the primary
evaluation target (2.10× at top 1% against `is_serious`), is
mechanistically explainable through direct policy citation, and is
stable under feature drift because its logic is explicit. Isolation
Forest surfaces HCPs whose statistical profile is anomalous but who do
not trigger rules — these are candidates for investigating novel
violation patterns not encoded in the current rulebook. COPOD provides
methodological cross-validation: its top-K slices overlap substantially
with IF's, which gives confidence that the anomaly signal reflects
structure in the data rather than a tree-based isolation artifact.

Deployment pattern:
- Rules flag alone → investigate as known-pattern compliance issue
- IF flag alone → investigate as novel-pattern candidate
- Rules and IF both flag → highest-priority investigation
- Neither flags → deprioritize, but do not dismiss (16.5% natural-
  threshold recall means absence of flag carries limited information)

Explanation mechanism per HCP:
- Rule flags with policy citation for rule-flagged HCPs
- SHAP values for IF-flagged HCPs
- COPOD per-feature tail percentiles as a complementary view,
  especially useful when SHAP and percentiles agree on top drivers

This is the recommended selection because it maximizes the distinct
information each detector provides and aligns with how compliance teams
actually operate — triaged investigation queues, not single-score
rankings.

The production dashboard implements a two-model subset of this
ensemble: rules + Isolation Forest combined as a weighted composite
(0.6 × rule_score + 0.4 × IF score), with a critical-flag floor that
promotes any HCP with a critical-severity rule to at least 'high'
tier regardless of composite. COPOD is not included in the
production scorer. IF and COPOD are highly correlated on this data
(Spearman ρ = 0.943, Pearson = 0.907, top-1% set overlap 73.9%,
top-5% overlap 74.3%), and their aggregate detection performance
against ground truth is statistically indistinguishable. COPOD's
role in this project is methodological confirmation — it
demonstrates that IF's anomaly signal reflects structure in the
data rather than a tree-based isolation artifact. Because the two
methods converge, adding COPOD to the production composite would
not measurably improve detection, and the marginal operational
complexity is unjustified. IF is preferred for production because
SHAP-based per-HCP explanation is a mature, well-established
workflow for tree-based anomaly detection.

### Fallback: Rules Engine Only

If deployment constraints require a single model — regulatory reviewers
demand binary yes/no explanations per flag, the MLOps environment
cannot support multiple scoring pipelines, or investigator training
bandwidth cannot absorb three explanation mechanisms — the rules
engine is the defensible single-model choice.

The justification is narrow but real. Rules achieve the highest lift
against `is_serious` at every threshold tested. Their explanation is
directly traceable to policy language, which is legally and
operationally important in a compliance context where flagged HCPs may
face consequences. They do not require feature engineering maintenance
beyond the rulebook itself, and their behavior is fully predictable
under input changes.

The cost of this choice must be recorded honestly: the rules engine
cannot surface novel patterns. Any HCP engaging in a violation category
not encoded in the current 23 rules will not be flagged, regardless of
how statistically extreme the behavior is. A rules-only deployment is
therefore a policy-enforcement system, not an anomaly-detection system.
If the organization's goal is enforcing known policy, this is
sufficient. If the goal includes discovering patterns the policy does
not yet cover, rules-only is inadequate and the ensemble is required.

### Why Not Single-Model IF or COPOD

Neither IF nor COPOD is defensible as a single-model choice on this
data. Both hover near lift = 1.0 on the primary evaluation target, and
their primary production value is conditional — they earn their place
by surfacing HCPs that rules miss. Without rules as the complementary
detector, their output is a near-random ranking of HCPs by statistical
extremity, which is not a useful compliance signal on its own.

### Decision

The project takes the three-model ensemble forward. Rules serve as the
primary screen, IF as the novel-pattern screen, COPOD as the
methodological confirmation layer. The fallback path to rules-only is
documented for organizational contexts where the ensemble cannot be
deployed, with the explicit acknowledgement that such a deployment
sacrifices novel-pattern detection.

## Tradeoffs

The rules engine offers high precision on known patterns and near-zero ability to surface new ones. Its explanation is binary — a rule fires or it doesn't — which is interpretable to investigators but does not support graded prioritization within the flagged set.

Isolation Forest captures magnitude-based anomalies that rules do not encode and provides graded, per-HCP explanation. Its 15% contamination parameter is a modeling choice that affects the top-K slice directly; future work should characterize sensitivity to this choice.

COPOD adds methodological diversity without measurable operational lift over IF on this dataset. Its value is interpretive (per-feature tail probabilities) and confirmatory (two independent methods converging).

## Limitations

The evaluation is on synthetic data calibrated to PhRMA and OIG 2020 frameworks. Behavior is modeled probabilistically, not observed, and the mapping from latent violator intent to emitted records is a modeling choice that real HCP populations may not follow. The labels evaluated against, including `hcp_violation_profile`, are generation-time assignments. Real investigator-confirmed labels would be noisier and more biased.

Features and labels are contemporaneous. No temporal holdout was performed, so the evaluation does not test generalization across time periods.

Rules were hand-specified; their selection reflects analyst judgment about what the PhRMA/OIG frameworks prioritize, not an exhaustive enumeration of regulatory text.

## Future Work

Phase 2 would introduce supervised learning once investigator outcomes have accumulated. Training targets would be investigator-confirmed violator status rather than the current synthetic profile. The comparison point would be the current ensemble, not raw rules or raw IF in isolation.

Phase 3 would introduce a LangGraph supervisor that routes queries between rules, IF, and COPOD agents based on query type — compliance policy check to rules, novel pattern discovery to IF, per-feature explanation to COPOD.

Additional refinements already backlogged: recency-weighted rule scores (2024 = 1.0, 2023 = 0.7, 2022 = 0.4), so that historical rule firings decay in influence; temporal train-test splits to measure generalization; SHAP computed on COPOD for cross-validation of feature importance across methods.
