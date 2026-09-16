import pandas as pd

from src.data_generator import GenerationConfig, generate_data


def test_generator_is_reproducible():
    config = GenerationConfig(seed=123, n_transactions=1000, exception_rate=0.05)
    first = generate_data(config)
    second = generate_data(config)

    for left, right in zip(first, second):
        pd.testing.assert_frame_equal(left, right)


def test_truth_contains_expected_exception_types():
    _, _, _, truth = generate_data(
        GenerationConfig(seed=42, n_transactions=1000, exception_rate=0.05)
    )
    expected = {
        "missing_gateway",
        "missing_settlement",
        "amount_mismatch",
        "status_mismatch",
        "duplicate_gateway",
        "duplicate_settlement",
        "fee_mismatch",
        "settlement_delay",
        "orphan_gateway",
        "orphan_settlement",
    }
    assert set(truth["exception_type"]) == expected
