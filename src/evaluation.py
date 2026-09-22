from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class EvaluationResult:
    summary: dict
    by_type: pd.DataFrame
    comparison: pd.DataFrame


def evaluate_detections(
    truth: pd.DataFrame,
    detected: pd.DataFrame,
) -> EvaluationResult:
    required_truth = {"transaction_id", "exception_type", "reference_amount"}
    required_detected = {"transaction_id", "exception_type", "amount_at_risk"}

    missing_truth = required_truth.difference(truth.columns)
    missing_detected = required_detected.difference(detected.columns)
    if missing_truth:
        raise ValueError(f"truth is missing required columns: {sorted(missing_truth)}")
    if missing_detected:
        raise ValueError(f"detected is missing required columns: {sorted(missing_detected)}")

    truth_keys = truth[["exception_type", "transaction_id", "reference_amount"]].copy()
    truth_keys["truth_present"] = True

    detected_keys = (
        detected[["exception_type", "transaction_id", "amount_at_risk"]]
        .drop_duplicates(["exception_type", "transaction_id"])
        .copy()
    )
    detected_keys["detected_present"] = True

    comparison = truth_keys.merge(
        detected_keys,
        on=["exception_type", "transaction_id"],
        how="outer",
    )
    comparison["truth_present"] = comparison["truth_present"].fillna(False).astype(bool)
    comparison["detected_present"] = comparison["detected_present"].fillna(False).astype(bool)

    comparison["outcome"] = "tn"
    comparison.loc[
        comparison["truth_present"] & comparison["detected_present"], "outcome"
    ] = "tp"
    comparison.loc[
        ~comparison["truth_present"] & comparison["detected_present"], "outcome"
    ] = "fp"
    comparison.loc[
        comparison["truth_present"] & ~comparison["detected_present"], "outcome"
    ] = "fn"

    tp = int((comparison["outcome"] == "tp").sum())
    fp = int((comparison["outcome"] == "fp").sum())
    fn = int((comparison["outcome"] == "fn").sum())

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )

    injected_amount = float(truth["reference_amount"].sum())
    captured_amount = float(
        comparison.loc[comparison["outcome"].eq("tp"), "reference_amount"]
        .fillna(0)
        .sum()
    )
    amount_capture_rate = captured_amount / injected_amount if injected_amount else 0.0

    type_rows = []
    all_types = sorted(
        set(truth["exception_type"].astype(str))
        | set(detected["exception_type"].astype(str))
    )
    for exc_type in all_types:
        subset = comparison.loc[comparison["exception_type"].eq(exc_type)]
        t_tp = int((subset["outcome"] == "tp").sum())
        t_fp = int((subset["outcome"] == "fp").sum())
        t_fn = int((subset["outcome"] == "fn").sum())
        t_precision = t_tp / (t_tp + t_fp) if (t_tp + t_fp) else 0.0
        t_recall = t_tp / (t_tp + t_fn) if (t_tp + t_fn) else 0.0
        t_f1 = (
            2 * t_precision * t_recall / (t_precision + t_recall)
            if (t_precision + t_recall)
            else 0.0
        )
        type_rows.append(
            {
                "exception_type": exc_type,
                "tp": t_tp,
                "fp": t_fp,
                "fn": t_fn,
                "precision": t_precision,
                "recall": t_recall,
                "f1": t_f1,
            }
        )

    by_type = pd.DataFrame(type_rows)

    summary = {
        "truth_exceptions": int(len(truth)),
        "detected_exceptions": int(
            detected.drop_duplicates(["exception_type", "transaction_id"]).shape[0]
        ),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "injected_reference_amount": round(injected_amount, 2),
        "captured_reference_amount": round(captured_amount, 2),
        "amount_capture_rate": amount_capture_rate,
    }

    return EvaluationResult(
        summary=summary,
        by_type=by_type,
        comparison=comparison.sort_values(
            ["exception_type", "transaction_id"], ignore_index=True
        ),
    )
