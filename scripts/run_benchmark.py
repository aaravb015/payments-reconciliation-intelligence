from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.data_generator import GenerationConfig, write_dataset
from src.evaluation import evaluate_detections
from src.reconciliation import reconcile


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the V1 synthetic reconciliation benchmark.")
    parser.add_argument("--output-dir", default="artifacts/v1_benchmark")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--transactions", type=int, default=10_000)
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    paths = write_dataset(
        output / "data",
        GenerationConfig(seed=args.seed, n_transactions=args.transactions),
    )

    internal = pd.read_csv(paths["internal"], parse_dates=["created_at"])
    gateway = pd.read_csv(paths["gateway"], parse_dates=["gateway_event_at"])
    settlement = pd.read_csv(paths["settlement"], parse_dates=["settled_at"])
    truth = pd.read_csv(paths["truth"])

    detected = reconcile(internal, gateway, settlement)
    result = evaluate_detections(truth, detected)

    detected.to_csv(output / "detected_exceptions.csv", index=False)
    result.by_type.to_csv(output / "metrics_by_type.csv", index=False)
    result.comparison.to_csv(output / "truth_vs_detection.csv", index=False)
    (output / "metrics.json").write_text(
        json.dumps(result.summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(result.summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
