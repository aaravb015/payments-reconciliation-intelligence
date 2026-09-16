# Payments Reconciliation Intelligence

**Which payments need investigation, how much money may be exposed, and where should operations act first?**

A reproducible, synthetic three-way reconciliation system for an internal payment ledger, gateway records and bank settlements. It demonstrates **financial operations automation, DuckDB/SQL data engineering, deterministic controls, exposure analysis and management reporting**.

> **Phase 2 is implemented on `dev-reconciliation-v1`. No merge to `main`.** This is a controlled synthetic portfolio project, not production certification, real-world accuracy, realized loss, or recovered cash.

## Operational workflow

| Stage | Implementation |
|---|---|
| Raw payment feeds | Existing deterministic generator; 10,000 intended INR payments over 30 days |
| SQL reconciliation | Schema validation, explicit UTC cutoff, DECIMAL amounts, duplicate-safe source aggregation and joins |
| Exception detection | Ten original rules plus bank gross/net discrepancies, executed in DuckDB SQL |
| Operational analytics | Merchant/day/method/provider counts, transaction rates, source totals and comparable deltas |
| Financial exposure | Separate financial discrepancy estimates, overdue cash and historical late cash |
| Prioritized investigation | One case per transaction; severity, current exposure, age, stable ranking and recommended actions |
| Management reporting | Self-contained HTML with charts; full CSV tables, JSON summary and audit manifest |

**Truth stays separate.** The operational engine accepts only the three source feeds. It projects an explicit column allowlist and never imports generation/evaluation or reads labels. Synthetic truth is used only by the separate benchmark/evaluation step.

## Quick start

Use Python 3.11–3.13. From a checkout of the development branch:

```bash
python -m pip install -r requirements.txt -c constraints.txt
python -m pytest -q
python scripts/run_benchmark.py --output-dir artifacts/v1_benchmark
python scripts/run_operations.py \
  --input-dir artifacts/v1_benchmark/data \
  --as-of 2026-02-10T00:00:00Z \
  --output-dir artifacts/phase2
```

Open **`artifacts/phase2/management_report.html`** in a browser. The operations command needs only `internal_payments.csv`, `gateway_transactions.csv` and `bank_settlements.csv`; it works without the truth file.

`--as-of` is mandatory. Future events are excluded from each feed. Missing settlement becomes overdue only after the elapsed three-day settlement window; payments still inside it are pending. A date-only cutoff means midnight at the start of that date.

To demonstrate the full workflow interactively:

```bash
python -m pip install -r requirements-demo.txt -c constraints.txt
python -m jupyterlab notebooks/reconciliation_demo.ipynb
```

[Open the demonstration notebook](notebooks/reconciliation_demo.ipynb). It walks through generation, SQL controls, exposure, concentration, queue prioritization, cutoff comparison, reporting and separate V1-rule evaluation. `python scripts/check_notebook.py` executes every cell in a fresh kernel. On a restricted runtime that disallows local kernel sockets, `--in-process` validates and executes the cells with IPython; CI still verifies a real kernel.

## Reference run: what the numbers mean

Fixed seed 42, 10,000 internal payments, cutoff **2026-02-10 00:00 UTC**:

| Measure | Result |
|---|---:|
| Deliberately injected V1 exceptions | 300 |
| V1 benchmark detections / false positives | 300 / 0 |
| Observed transaction IDs across all feeds | 10,060 |
| Transactions flagged by Phase 2 | 300 |
| Phase 2 rule signals | 330 |
| Overall exception transaction rate | 2.9821% |
| Internal-ledger exception rate | 2.4000% |
| Estimated current financial discrepancy exposure | ₹215,440.49 |
| Overdue expected net cash | ₹65,991.77 |
| Combined estimated current exposure | ₹281,432.26 |
| Historical late cash, excluded from current exposure | ₹49,875.26 |

The 60 extra transaction IDs are synthetic orphans. The 30 extra signals are bank net mismatches on the existing fee defects; they do not mean 30 newly injected cases. Rule signals can overlap, so exposure is deduplicated at transaction level. The 300/300 benchmark tests deliberately constructed rules on controlled synthetic data. **It must not be described as real-world detection accuracy.**

These exposure figures are operational review estimates. They are not realized loss, cash recovered, savings, or a sum of every rule's amount at risk. See [the exposure formulas and metric definitions](docs/phase2_operations.md).

## Reports

The operational run writes:

- `reconciliation_facts.csv`: one row per observed transaction, source counts, gross/net differences and settlement state.
- `detected_exceptions.csv`: all rule signals with reasons and monetary classifications.
- `investigation_queue.csv`: one ranked row per flagged transaction, combining overlapping rules.
- `merchant_metrics.csv`, `daily_metrics.csv`, `payment_method_metrics.csv`, `gateway_metrics.csv`: volumes, counts, rates, source totals, exposure and concentration shares.
- `exception_type_metrics.csv`, `aging_metrics.csv`, `settlement_delay_metrics.csv`, `daily_settlements.csv`: operational breakdowns and actual cash-date controls.
- `summary.csv`, `summary.json`, `management_report.html`, `manifest.json`: management outputs and reproducibility evidence.

Source totals retain duplicates and orphans for auditability; comparable deltas only use unambiguous matches. Daily payment-cohort metrics and daily bank settlement totals use different dates explicitly. Empty denominators are undefined, not forced to zero.

The V1 benchmark continues to write its original dataset, detections and evaluation artifacts. All bulk generated outputs are ignored by Git. CI tests Python 3.11/3.13, runs both command-line workflows, executes the notebook, and retains downloadable operational report artifacts.

## Repository guide

| Path | Purpose |
|---|---|
| [`docs/project_design.md`](docs/project_design.md) | Preserved V1 design contract |
| [`docs/phase2_operations.md`](docs/phase2_operations.md) | Operational contracts, formulas, limitations and reproduction instructions |
| `src/data_generator.py` | Unchanged seeded synthetic world and separate truth |
| `src/reconciliation.py` | Preserved V1 Python detector |
| `src/evaluation.py` | Separate controlled-benchmark evaluation |
| `src/operations.py` | Validated, truth-blind DuckDB orchestration |
| `sql/01_reconciliation.sql` | Duplicate-safe source rollups and gross/net reconciliation |
| `sql/02_exceptions.sql` | SQL exception rules and exposure classification |
| `sql/03_analytics.sql` | Metrics, aging, settlement analysis and queue ranking |
| `src/reporting.py` | Reproducible management outputs |
| `scripts/` | V1 benchmark, operational runner, notebook verification |
| `notebooks/reconciliation_demo.ipynb` | Executable demonstration |
| `tests/` | Baseline, SQL, operational controls and reporting tests |

## Controls and limitations

- Deterministic rules and SQL are the core; no machine learning has been added.
- Current exposure uses the maximum financial discrepancy estimate per transaction plus overdue cash. This avoids overlapping signals but can understate independent discrepancies on the same payment.
- Cash already received late is historical timing exposure, never outstanding cash. Status disagreements carry no direct monetary estimate in operational totals.
- Required source fields are validated; money uses INR/paise precision. Exact transaction IDs are the matching key.
- V1 has no provider identity, so gateway concentration is `unknown` unless an input supplies `gateway_id`.
- This is an event-time snapshot without ingestion history, FX, refunds, partial/split settlements, business-day calendars, real integrations, automated recovery, or persistent case ownership/resolution. Duplicate rows are not proof of duplicate cash movement.
- Queue age is a documented source-event/due-date proxy; `open_review` is a generated investigation state, not a real dispute-status assertion.
- Fixed inputs/cutoff/config and the same dependency versions reproduce report bytes. Direct dependency pins and manifest hashes support auditability.

This complements a modelling/risk portfolio by demonstrating an operational data and controls workflow. Further work should be driven by richer source contracts and realistic operational ambiguity, rather than adding machine learning for appearance.

MIT license; see [LICENSE](LICENSE).
