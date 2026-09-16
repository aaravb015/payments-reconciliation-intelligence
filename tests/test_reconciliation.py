from src.data_generator import GenerationConfig, generate_data
from src.evaluation import evaluate_detections
from src.reconciliation import reconcile


def test_reconciliation_recovers_injected_v1_benchmark():
    internal, gateway, settlement, truth = generate_data(
        GenerationConfig(seed=42, n_transactions=2000, exception_rate=0.03)
    )
    detected = reconcile(internal, gateway, settlement)
    result = evaluate_detections(truth, detected)

    assert result.summary["precision"] == 1.0
    assert result.summary["recall"] == 1.0
    assert result.summary["amount_capture_rate"] == 1.0


def test_reconciliation_signature_does_not_need_truth():
    internal, gateway, settlement, _ = generate_data(
        GenerationConfig(seed=7, n_transactions=500, exception_rate=0.04)
    )
    detected = reconcile(internal, gateway, settlement)
    assert not detected.empty
