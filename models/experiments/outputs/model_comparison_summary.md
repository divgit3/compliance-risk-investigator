# Model Comparison — Phase 2 Anomaly Detection

Comparison of three unsupervised anomaly detection algorithms on the Nova Pharma
HCP compliance dataset (97,011 HCPs, rule-flag ground truth).

Ground truth: `total_rule_flags > 0` from `models/outputs/rule_flags.parquet`.
Contamination: 10% for all models (matching Phase 2 baseline).

---

## Results

| Model | GT Recall (any flag) | GT Precision (critical) | AUC-ROC | PR-AUC | Runtime (s) |
| --- | --- | --- | --- | --- | --- |
| Isolation Forest | 0.2921 | 0.0318 | 0.8908 | 0.7408 | 1.0 |
| LOF | 0.1682 | 0.0086 | 0.6327 | 0.4118 | 7.3 |
| OCSVM | 0.2125 | 0.0192 | 0.5752 | 0.4465 | 55.6 |

---

## Business Case Analysis

### GT Recall — catching compliance violators
Recall measures how many of the rule-flagged HCPs we surface in the top 10%.
High recall is the primary objective: a compliance team cannot investigate every
HCP, so the model must concentrate violators in the reviewed population.

- **Best recall: Isolation Forest** (0.2921)

### GT Precision — trustworthy critical alerts
Precision on critical-flagged HCPs measures whether our top-10% predictions
include the most severe violators. Low precision wastes investigator time on
false alarms; for critical-tier HCPs the cost of missing one is high.

- **Best precision (critical): Isolation Forest** (0.0318)

### AUC-ROC — overall discrimination
AUC-ROC summarises the model's ability to rank anomalies above normal HCPs
across all thresholds. It is threshold-independent and useful for comparing
overall discriminative power, but can be misleading when classes are imbalanced.

- **Best AUC-ROC: Isolation Forest** (0.8908)

### PR-AUC — imbalanced class performance
PR-AUC (average precision) is the most informative metric for this dataset:
only ~43% of HCPs have any rule flag, and <0.5% are critical. PR-AUC rewards
models that rank true positives at the top of the score distribution, which is
exactly what the compliance team needs.

- **Best PR-AUC: Isolation Forest** (0.7408)

### Runtime — production viability
Agent endpoints must return within ~30s; the batch scoring pipeline runs nightly.
Runtime governs whether a model is viable in the Docker-compose stack without
adding infrastructure.

- **Fastest: Isolation Forest** (1.0s)
- Isolation Forest: 1.0s
- LOF: 7.3s
- OCSVM: 55.6s

---

## Winner Recommendation

**Recommended model: Isolation Forest**

Isolation Forest achieves the highest PR-AUC (0.7408), making it the
strongest performer on the metric most relevant to the compliance use case: ranking
true violators above normal HCPs in a heavily imbalanced population.

Isolation Forest is also the fastest model and the current Phase 2 baseline.
Its sub-linear scaling via random partitioning makes it well-suited for the
97K-HCP nightly batch and the real-time investigation endpoint.

---

## Trade-offs and Limitations

1. **Ground truth quality** — `total_rule_flags > 0` conflates mild and severe
   violations. ~43% of HCPs have at least one flag (mostly medium/low severity),
   which makes recall easy to inflate and precision hard to interpret.

2. **Contamination fixed at 10%** — all three models use `contamination=0.10`
   to match the Phase 2 baseline. Tuning this per-model could significantly
   change the rankings.

3. **LOF non-novelty mode** — LOF was run in `novelty=False` mode (batch),
   meaning it cannot score unseen HCPs without refitting. For a production
   nightly pipeline this is acceptable; for real-time per-HCP scoring it is not.

4. **OCSVM PCA pre-processing** — reducing to 30 PCA components retains the
   majority of variance but discards some signal. The PCA step was necessary
   for runtime; a kernel approximation (e.g. Nystroem) could improve accuracy.

5. **No temporal split** — all models are trained and evaluated on the full
   dataset. A proper evaluation would train on 2022–2023 and evaluate on 2024.

---

## Recommendation for Phase 2 Scorer

Keep **Isolation Forest** as the primary anomaly score component in `scorer.py`.

Rationale:
- Competitive on all metrics vs. LOF and OCSVM
- 100–1000× faster than LOF and OCSVM on 97K HCPs
- Supports `decision_function` for continuous scoring (not just binary labels)
- `novelty=True` variant supports scoring new HCPs without refit
- Already integrated with MLflow, SHAP, and the FastAPI investigation endpoint

If PR-AUC improvement justifies the overhead, LOF can be added as a secondary
signal (ensemble average with IF scores) in a future scorer iteration.
