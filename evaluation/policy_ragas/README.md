# Policy Agent RAGAS Evaluation

End-to-end RAGAS evaluation suite for the Policy Agent's RAG layer.

- `golden_dataset.json` — 10-15 grounded compliance questions with
  reference answers from the 5 policy PDFs (constructed in Stage 2)
- `run_evaluation.py` — test harness that queries the API and runs
  RAGAS metrics (Faithfulness, Retrieval Relevance, Answer Relevancy)
- `results/` — output directory for evaluation runs

Run: `python -m evaluation.policy_ragas.run_evaluation`
