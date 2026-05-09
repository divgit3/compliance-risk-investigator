# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""Precision/recall/lift evaluation utilities for IF vs rule-score comparison."""

import numpy as np
import pandas as pd


def evaluate_score(
    score_col: str,
    label_col: str,
    merged_df: pd.DataFrame,
    top_k_pcts: tuple = (0.01, 0.05, 0.10),
) -> dict:
    """Compute precision/recall/F1/lift at each top-K% threshold."""
    df = merged_df[[score_col, label_col]].copy()
    n_total = len(df)
    total_positives = int(df[label_col].sum())
    base_rate = total_positives / n_total

    df_sorted = df.sort_values(score_col, ascending=False, kind="mergesort")
    labels_sorted = df_sorted[label_col].values

    results_by_k = []
    for k_pct in top_k_pcts:
        n_flagged = int(np.ceil(n_total * k_pct))
        tp = int(labels_sorted[:n_flagged].sum())
        precision = tp / n_flagged if n_flagged > 0 else 0.0
        recall = tp / total_positives if total_positives > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        lift = precision / base_rate if base_rate > 0 else 0.0
        results_by_k.append(
            {
                "k_pct": k_pct,
                "n_flagged": n_flagged,
                "tp": tp,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "lift": lift,
            }
        )

    result = {
        "score_col": score_col,
        "label_col": label_col,
        "total_positives": total_positives,
        "base_rate": base_rate,
        "results_by_k": results_by_k,
    }

    if score_col == "rule_score":
        subset = df[df[score_col] > 0]
        n_flagged_nat = len(subset)
        tp_nat = int(subset[label_col].sum())
        precision_nat = tp_nat / n_flagged_nat if n_flagged_nat > 0 else 0.0
        recall_nat = tp_nat / total_positives if total_positives > 0 else 0.0
        f1_nat = (
            2 * precision_nat * recall_nat / (precision_nat + recall_nat)
            if (precision_nat + recall_nat) > 0
            else 0.0
        )
        lift_nat = precision_nat / base_rate if base_rate > 0 else 0.0
        effective_pct = n_flagged_nat / n_total
        result["natural_threshold"] = {
            "n_flagged": n_flagged_nat,
            "tp": tp_nat,
            "precision": precision_nat,
            "recall": recall_nat,
            "f1": f1_nat,
            "lift": lift_nat,
            "threshold_label": "natural (rule_score > 0)",
            "effective_pct": effective_pct,
        }

    return result


def to_comparison_rows(eval_result: dict) -> list[dict]:
    """Flatten one eval_result into tidy rows for a comparison DataFrame."""
    model = eval_result["score_col"]
    label = eval_result["label_col"]
    base_rate = eval_result["base_rate"]

    rows = []
    for r in eval_result["results_by_k"]:
        pct_label = f"top {int(r['k_pct'] * 100)}%"
        rows.append(
            {
                "model": model,
                "label": label,
                "threshold": pct_label,
                "n_flagged": r["n_flagged"],
                "tp": r["tp"],
                "precision": r["precision"],
                "recall": r["recall"],
                "f1": r["f1"],
                "lift": r["lift"],
                "base_rate": base_rate,
            }
        )

    if "natural_threshold" in eval_result:
        nt = eval_result["natural_threshold"]
        effective_pct = nt["effective_pct"] * 100
        rows.append(
            {
                "model": model,
                "label": label,
                "threshold": f"natural ({effective_pct:.1f}%)",
                "n_flagged": nt["n_flagged"],
                "tp": nt["tp"],
                "precision": nt["precision"],
                "recall": nt["recall"],
                "f1": nt["f1"],
                "lift": nt["lift"],
                "base_rate": base_rate,
            }
        )

    return rows
