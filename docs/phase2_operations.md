# Phase 2 — operational reconciliation contract

Phase 2 extends the committed V1 design and preserves its generator, Python detector, evaluation module, and ten-rule benchmark. Its operational path is separate and truth-blind. No machine learning is involved.

## Workflow and boundaries

1. Raw CSVs: internal ledger, gateway records, bank settlements.
2. `src/operations.py`: validate explicit source contracts, normalize timestamps to UTC, project only allowlisted columns, filter events to an explicit inclusive cutoff, and load DuckDB DECIMAL monetary fields.
3. `sql/01_reconciliation.sql`: source rollups, union of transaction IDs, duplicate-safe reconciliation facts and comparable gross/net deltas.
4. `sql/02_exceptions.sql`: ten original rule categories plus bank gross and bank net discrepancies; explicit overdue versus pending treatment.
5. `sql/03_analytics.sql`: transaction-level exposure, investigation queue, concentration, daily/merchant metrics, delay and aging tables. Reused intermediate relations are materialized once per snapshot; final reporting views are queried from SQL.
6. `src/reporting.py`: reproducible CSV/JSON and a self-contained HTML management report with charts. Python formats results; SQL performs detection, aggregation and ranking.
7. `src/evaluation.py`: separate evaluation against labels, after detection, only when explicitly invoked by the benchmark/demo.

The operational CLI reads exactly three known filenames. It neither imports generation/evaluation nor opens a truth file. Additional input columns, including synthetic labels, are discarded before warehouse registration. No generated truth can enter a SQL relation. The notebook performs generation and evaluation in separate cells around the operational call.

## Run from a clean checkout

Python 3.11–3.13 is supported; CI tests 3.11 and 3.13.

```bash
git clone --branch dev-reconciliation-v1 https://github.com/aaravb015/payments-reconciliation-intelligence.git
cd payments-reconciliation-intelligence
python -m venv .venv
# Linux/macOS; Windows: .venv\Scripts\activate
source .venv/bin/activate
python -m pip install -r requirements.txt -c constraints.txt
python -m pytest -q
python scripts/run_benchmark.py --output-dir artifacts/v1_benchmark
python scripts/run_operations.py --input-dir artifacts/v1_benchmark/data --as-of 2026-02-10T00:00:00Z --output-dir artifacts/phase2
```

Open `artifacts/phase2/management_report.html` in a browser. It needs no server or external assets. Run operations against any directory with compatible `internal_payments.csv`, `gateway_transactions.csv`, and `bank_settlements.csv`; truth is optional and ignored. The command also works from another working directory when given the absolute script/input/output paths.

```bash
python -m pip install -r requirements-demo.txt -c constraints.txt
python -m jupyterlab notebooks/reconciliation_demo.ipynb
# Execute and validate every cell in a fresh kernel:
python scripts/check_notebook.py
# Restricted environments without local kernel sockets can execute cells in process:
python scripts/check_notebook.py --in-process
```

Generated datasets and notebook/report outputs remain under ignored `artifacts/`. The committed notebook has no execution outputs.

## Source contracts and cutoff

The original V1 fields remain compatible. Operations additionally requires ledger `merchant_id`, `payment_method`, and `currency` for trustworthy grouping. Required identifiers, dimensions, statuses and timestamps cannot be null or blank. The internal transaction key must be unique. Monetary inputs must be finite, nonnegative, below ₹10^13, and have at most two decimal places. These constraints deliberately exclude refunds/negative adjustments. Internal currency must be INR; the other two feeds are assumed to follow the same INR contract.

`gateway_id` is optional on the gateway feed. Missing provider data becomes `unknown`; multiple providers on a duplicate transaction become `multiple`. Provider identity is not inferred. The unchanged V1 generator therefore produces an unknown-provider concentration row. Orphan merchant/payment-method attribution remains `unknown`.

`--as-of` is mandatory. Naive times mean UTC; offset-aware inputs are normalized to UTC. Each source is independently filtered to event timestamps `<= as_of`. A date-only cutoff is **midnight at the start of that date**, not end of day. There is no use of wall-clock time. Row counts before/after filtering are audited.

The three-day SLA is an elapsed **calendar** interval from the unique successful gateway event. Missing settlement is flagged only when the internal and unique gateway records both say success, no bank record is present, and `as_of > due_at`. Equality is still pending. Duplicate feeds are ambiguous and excluded from comparisons requiring unique records. Settlements received after the due time are historical SLA breaches. Their timing exposure is never current outstanding cash.

This is an **event-time snapshot**, not an ingestion-time replay: late-arriving records cannot be reconstructed without ingestion timestamps. Full production status lifecycle/failed-payment settlement controls, refund/split settlement semantics, and matching on secondary keys are outside this phase.

## Rules and estimated exposure

All source amounts are converted to DuckDB `DECIMAL(18,2)` before arithmetic. Default gross/net tolerance and fee tolerance are ₹0.05, strictly exceeded to flag. Configuration values must be finite and nonnegative. Monetary fields in CSVs have six decimal places for consistent numeric serialization; amounts remain paise-based.

| Rule | Raw rule amount at risk | Operational treatment |
|---|---|---|
| missing_gateway | Internal gross | Financial discrepancy estimate if no bank line; zero direct exposure when a bank line is present |
| missing_settlement | Expected gateway net | Current overdue cash; not also added as financial discrepancy exposure |
| amount_mismatch | Absolute internal/gateway gross delta | Financial discrepancy estimate |
| status_mismatch | Internal gross (V1 compatibility) | Data-integrity review; zero direct financial exposure |
| duplicate_gateway | Sum of gateway gross minus the largest row | Conservative excess estimate; assumes the largest row is legitimate |
| duplicate_settlement | Sum of bank net minus the largest row | Conservative excess estimate; assumes the largest row is legitimate |
| fee_mismatch | Absolute fee difference + absolute tax difference | Financial discrepancy estimate |
| settlement_delay | Expected gateway net | Historical late cash only; zero current exposure |
| orphan_gateway | Gateway gross | Unattributed financial discrepancy estimate |
| orphan_settlement | Bank net | Unattributed financial discrepancy estimate |
| settlement_gross_mismatch | Absolute gateway/bank gross delta | Financial discrepancy estimate |
| settlement_net_mismatch | Absolute gateway/bank net delta | Financial discrepancy estimate; may overlap fee/gross signals |

The two added categories are valid control signals, not newly labeled synthetic defects. A fee defect also changes net and can trigger both rules. V1 core-rule comparison filters to the original ten categories; do not evaluate all Phase 2 signals against incomplete V1 truth labels.

**Per-transaction current exposure** = maximum financial discrepancy estimate across its rules + overdue expected net cash. Each transaction contributes once to portfolio totals. This prevents overlapping rule signals from inflating exposure, but can understate independent losses on one transaction. It is a conservative prioritization proxy, not a proven bound on actual losses. Rule-level amounts in `exception_type_metrics.csv` are intentionally non-additive across categories. Cash received late is separately reported as historical late cash. A duplicate feed row does not prove duplicate fund movement; an orphan credit may be unallocated cash rather than loss.

## Metrics and denominators

| Field/output | Definition |
|---|---|
| observed_transactions | Distinct transaction IDs across the union of all three filtered feeds |
| internal_payments | Distinct internal ledger payments |
| orphan_transactions | Observed IDs with no internal payment; one ID counted once even if in both other sources |
| exception_count | Number of (transaction ID, rule) signals; can exceed payment count |
| exception_transactions | Distinct IDs with at least one signal |
| exception_transaction_rate | Exception transactions / observed transactions, including orphans |
| internal_exception_rate | Flagged internal payments / internal payments; excludes orphans in both numerator and denominator |
| exposure_share | Dimension's current exposure / total current exposure |
| exception_share | Dimension's flagged transaction count / total flagged transaction count |
| matched_payments | No exception, one internal payment, one gateway record, and one bank settlement |
| source gross/net totals | Sum of actual source rows, retaining duplicates and orphans; not expected to agree blindly |
| comparable deltas | Gateway minus internal, or bank minus gateway; only uniquely comparable records participate |
| absolute comparable deltas | Sum of absolute per-transaction deltas, so positive and negative discrepancies cannot cancel |
| comparable payment counts | Number of eligible unique comparisons behind the respective deltas |
| daily_metrics | Activity cohort: internal creation date; gateway event date or bank date for orphans |
| daily_settlements | Actual bank cash-date totals, including all raw lines |
| settlement_delay_metrics | Mean, median, p95, maximum and late counts on unique successful gateway/bank pairs; pending/overdue counts separately |

All concentration tables carry amounts, counts and rates so volume can be distinguished from exception incidence. Empty denominators are null, not a fabricated zero rate. Empty inputs still produce a one-row zero-count summary. Daily tables contain observed dates; absent dates are not padded. Internal gross is not an internal net settlement figure: V1 has no internal expected fee/net contract. Net is therefore reconciled between gateway and bank.

## Aging and investigation queue

One queue row per flagged transaction, containing all rule names, provider/merchant/method, severity, current and historical amounts, reason/action, and a stable priority rank.

- Severity = highest triggered severity: critical 4, high 3, medium 2, low 1.
- Score = severity × 100 + `min(current_exposure_inr / 10000, 30)` + `min(age_days, 30)`.
- Ties: current exposure descending, age descending, transaction ID ascending.
- Overdue cash age = days beyond expected settlement due time.
- Historical late settlement age = actual settlement delay beyond SLA; stops growing once settled.
- Other exception age = days since source activity. This is a proxy, not first-detected age or case-resolution age.
- A multi-rule case uses the maximum applicable age and highest severity.
- Buckets: zero; (0,3]; (3,7]; (7,14]; (14,30]; over 30 elapsed days.
- `settled_late_review` means the only signal is a historical SLA breach. Other cases are `open_review` snapshots. No claim is made about an actual human investigation's resolution state.

## Reproducibility and outputs

`constraints.txt` pins tested direct dependencies; the manifest records actual Python, NumPy, pandas and DuckDB versions. Seeded generation is unchanged. Fixed source bytes/config/cutoff and the same runtime produce byte-identical CSV, JSON, HTML and manifest outputs. SQL source hashes, Python operational/reporting hashes, source CSV hashes, run parameters, cutoff, source counts and output hashes are recorded. No wall-clock run time or absolute output path enters report content. Different dependency/runtime versions are not promised byte-identical serialization.

Twelve CSVs: reconciliation facts; detected exceptions; queue; summary; merchant, day, method and provider metrics; rule breakdown; aging; settlement delays; daily cash settlements. Also `summary.json`, `management_report.html`, and `manifest.json`. They are snapshots, not a persisted database. CI runs both command-line flows and uploads the operational report for review.

## Validation and limitations

Tests cover hand-calculated gross/net and fee discrepancies, duplicate join protection, multiple duplicate amounts, pending/due/late boundaries, future-row exclusion, UTC equivalence, financial overlap, aging bins, priority order, unknown attribution, rate denominators, aggregate tie-outs, empty sources, schema failures, shuffled-row determinism, truth-column isolation, malformed/absent truth-file independence, report escaping, manifest hashes and repeatable report bytes. Three seeds compare SQL core rules to V1's detector on matured snapshots. The full notebook executes in CI.

Synthetic inputs are simplified and intentionally controlled. No external payment system, banking API, FX, business calendar, real merchant information, automated fund movement, guaranteed recovery, persistent case resolution, or real-world accuracy claim is included. This phase demonstrates an auditable operational workflow rather than production certification.
