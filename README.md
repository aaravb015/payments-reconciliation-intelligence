# Payments Reconciliation Intelligence

**Can three-way payment reconciliation automatically surface operational exceptions and quantify money at risk?**

A synthetic, reproducible reconciliation system comparing an internal payment ledger, payment-gateway records, and bank-settlement records. The project is designed to demonstrate Python/SQL data work, deterministic exception detection, financial-impact estimation, testing, and investigation-focused reporting.

> **Status:** V1 is under development on `dev-reconciliation-v1`. The current benchmark is synthetic and intentionally controlled; it is not a claim of real-world payment accuracy or recovered financial loss.

## V1 architecture

```text
Synthetic payment world
        |
        +--> Internal payment ledger
        +--> Gateway transaction feed
        +--> Bank settlement feed
        |
        +--> Ground-truth injected defects (evaluation only)
                         |
Three-way reconciliation engine
        |
        +--> Presence / orphan checks
        +--> Duplicate checks
        +--> Amount / status checks
        +--> Fee / tax checks
        +--> Settlement timing checks
                         |
Normalized exception queue
        |
        +--> severity
        +--> amount at risk
        +--> exposure kind
        +--> investigation reason
                         |
Independent benchmark evaluation
```

The reconciliation engine accepts only the three operational source tables. The synthetic truth file is kept separate and is used only after detection to measure precision, recall, F1, and amount capture.

## V1 exception taxonomy

- `missing_gateway`
- `missing_settlement`
- `amount_mismatch`
- `status_mismatch`
- `duplicate_gateway`
- `duplicate_settlement`
- `fee_mismatch`
- `settlement_delay`
- `orphan_gateway`
- `orphan_settlement`

The default generator creates 10,000 intended INR payments over 30 days and injects approximately 3% labeled reconciliation defects across those ten categories.

## Current controlled benchmark

With the default seed and current frozen V1 defect definitions, the deterministic engine recovers all **300 / 300** injected exceptions with no false positives.

That result should be interpreted narrowly: the injected defects are deliberately designed to test whether the corresponding reconciliation rules work. It is a software/logic benchmark, not evidence that the system would achieve perfect accuracy on production payment data. Later phases will add benign ambiguity, operational aggregates, and anomaly-assisted prioritization.

## Quick start

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python scripts/run_benchmark.py --output-dir artifacts/v1_benchmark
```

The benchmark writes:

- `data/internal_payments.csv`
- `data/gateway_transactions.csv`
- `data/bank_settlements.csv`
- `data/truth_exceptions.csv`
- `detected_exceptions.csv`
- `metrics.json`
- `metrics_by_type.csv`
- `truth_vs_detection.csv`

Bulk generated artifacts are intended to remain local unless deliberately reduced to small portfolio samples.

## Repository guide

| Path | Purpose |
|---|---|
| `docs/project_design.md` | V1 design contract, data contracts, exception definitions, success criteria |
| `src/data_generator.py` | Deterministic synthetic payment world and injected defects |
| `src/reconciliation.py` | Truth-blind three-way reconciliation and normalized exception queue |
| `src/evaluation.py` | Precision/recall/F1 and reference-amount capture evaluation |
| `scripts/run_benchmark.py` | End-to-end command-line benchmark |
| `tests/` | Reproducibility and reconciliation tests |

## Design principles

**Auditability over novelty.** Deterministic reconciliation rules come first. Machine learning or anomaly detection will only be added after the baseline is stable and interpretable.

**No label leakage.** The detector has no truth-file input. Ground truth exists only so the synthetic benchmark can be objectively evaluated.

**Financial language is conservative.** `amount_at_risk` is a transparent operational estimate. It is not equivalent to realized loss, prevented loss, recovered cash, or financial savings.

## Roadmap

V1 Phase 1 establishes the deterministic benchmark. Phase 2 will add DuckDB/SQL operational views, merchant/day summaries, aging and exposure reporting, and a demonstration notebook. An optional Phase 3 can then test anomaly-assisted prioritization against the deterministic baseline.

See [`docs/project_design.md`](docs/project_design.md) for the full specification.

MIT license; see [LICENSE](LICENSE).
