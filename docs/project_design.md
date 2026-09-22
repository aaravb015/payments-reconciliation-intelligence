# Payments Reconciliation Intelligence — V1 Design

## 1. Project question

Can a reproducible reconciliation system combine internal payment records, payment-gateway records, and bank-settlement records to identify operational exceptions, quantify money at risk, and prioritize investigation without using hidden ground-truth labels during detection?

This is a synthetic portfolio project. It is designed to demonstrate reconciliation logic, SQL/Python data work, exception handling, financial-impact calculation, testing, and operations-focused reporting. It is not a production certification or evidence about any real payment processor.

## 2. V1 objective

Build an end-to-end system that:

1. generates realistic but fully synthetic payment data;
2. creates three independently represented sources: internal payments, gateway transactions, and bank settlements;
3. injects known reconciliation defects into those sources;
4. runs a detection engine that does not have access to the injected labels;
5. produces an exception queue with type, severity, amount at risk, and investigation context;
6. evaluates detected exceptions against a separate ground-truth file;
7. exposes SQL queries and a notebook/demo for operational analysis;
8. is deterministic and testable from a fixed seed.

## 3. Non-objectives for V1

V1 will not claim live payment processing, automated fund recovery, production-grade ledger accounting, real bank/gateway integrations, or real-world detection accuracy. Machine learning is not required for the core reconciliation engine. Aggregate anomaly detection may be added only after deterministic reconciliation is working and evaluated.

## 4. Source systems

### 4.1 Internal payment ledger

One intended payment per order.

Core fields:

- `transaction_id`
- `order_id`
- `merchant_id`
- `customer_id`
- `created_at`
- `currency`
- `payment_method`
- `internal_status`
- `gross_amount`

### 4.2 Gateway transaction feed

Represents the processor/gateway view of the payment.

Core fields:

- `gateway_record_id`
- `transaction_id`
- `order_id`
- `gateway_event_at`
- `gateway_status`
- `gateway_amount`
- `fee_rate`
- `gateway_fee`
- `gateway_tax`
- `gateway_net`

### 4.3 Bank settlement feed

Represents money actually reported as settled.

Core fields:

- `settlement_line_id`
- `settlement_batch_id`
- `transaction_id`
- `settled_at`
- `gross_amount`
- `fee_amount`
- `tax_amount`
- `net_amount`

### 4.4 Ground-truth exceptions

Generated only for evaluation. The reconciliation engine must never read this file while detecting exceptions.

Core fields:

- `truth_exception_id`
- `transaction_id`
- `exception_type`
- `injected_source`
- `reference_amount`
- `notes`

## 5. Initial exception taxonomy

V1 will support the following deliberately injected problems.

| Exception | Description | Primary comparison |
|---|---|---|
| `missing_gateway` | Internal payment exists but gateway record is missing | internal vs gateway |
| `missing_settlement` | Successful payment has no bank settlement | internal/gateway vs bank |
| `amount_mismatch` | Gateway amount differs from internal amount | internal vs gateway |
| `status_mismatch` | Internal and gateway statuses disagree | internal vs gateway |
| `duplicate_gateway` | Multiple gateway records exist for one transaction | gateway uniqueness |
| `duplicate_settlement` | Multiple bank settlement lines exist for one transaction | settlement uniqueness |
| `fee_mismatch` | Settled fee/tax differs materially from expected gateway fee/tax | gateway vs bank |
| `settlement_delay` | Settlement occurs outside the allowed settlement window | gateway vs bank timing |
| `orphan_gateway` | Gateway transaction has no internal payment | gateway vs internal |
| `orphan_settlement` | Bank settlement has no internal payment | bank vs internal |

The simulator should prefer non-overlapping injected exceptions for the core benchmark so that evaluation is interpretable. Some naturally co-occurring signals may still appear and must be documented if they do.

## 6. Matching and reconciliation rules

V1 uses `transaction_id` as the primary exact-match key.

The engine will:

1. validate source schemas and key nullability;
2. identify duplicates before one-to-one joins;
3. reconcile unique internal and gateway records;
4. reconcile successful gateway/internal records to settlement lines;
5. compare amounts using an explicit INR tolerance;
6. compare expected gateway fee/tax with bank-reported fee/tax using an explicit tolerance;
7. test settlement age against a configured maximum delay;
8. emit one normalized exception record per detected exception type and transaction.

No ground-truth labels may be used to tune individual transactions.

## 7. Severity and exposure

Each detected exception will include:

- `severity`: `low`, `medium`, `high`, or `critical`;
- `amount_at_risk`: a transparent rule-based estimate, not a claim of realized financial loss;
- `exposure_kind`: e.g. `potential_loss`, `cash_timing`, or `data_integrity`;
- `reason`: a concise human-readable explanation.

Examples:

- missing settlement: expected net settlement amount;
- duplicate settlement: duplicated net amount;
- amount mismatch: absolute gross-amount difference;
- fee mismatch: excess absolute fee/tax difference;
- settlement delay: expected net amount tagged as cash-timing exposure;
- pure data-integrity mismatches may carry zero direct monetary exposure while retaining severity.

## 8. Synthetic world

Default V1 dataset target:

- 30 calendar days;
- 20 merchants;
- 2,000 customers;
- 10,000 intended payments;
- INR only;
- payment methods: card, UPI, net banking, wallet;
- deterministic fixed seed;
- realistic transaction-size skew rather than uniform amounts;
- method-specific fee rates;
- normal settlement delay concentrated around T+1/T+2;
- approximately 2–4% total injected reconciliation exceptions.

Exact injection rates will be configuration constants and will be frozen before the benchmark result is reported.

## 9. Evaluation contract

Detection is evaluated on the exact key:

`(exception_type, transaction_id)`

Primary metrics:

- overall exception precision;
- overall exception recall;
- F1 score;
- per-exception-type precision and recall;
- total injected reference amount;
- amount-at-risk surfaced by correctly detected exceptions;
- false positives by exception type.

The benchmark should also report source row counts and reconciled transaction counts so that coverage is auditable.

## 10. Repository structure

```text
payments-reconciliation-intelligence/
├── README.md
├── LICENSE
├── requirements.txt
├── data/
│   └── synthetic/
├── docs/
│   └── project_design.md
├── notebooks/
│   └── reconciliation_demo.ipynb
├── sql/
│   └── reconciliation_queries.sql
├── src/
│   ├── __init__.py
│   ├── data_generator.py
│   ├── reconciliation.py
│   ├── evaluation.py
│   └── reporting.py
├── artifacts/
└── tests/
```

Generated bulk data should not be committed unless deliberately reduced to small demonstration samples.

## 11. Implementation phases

### Phase 1 — deterministic benchmark

- synthetic data generator;
- injected exception truth set;
- deterministic reconciliation engine;
- evaluation module;
- unit tests;
- command-line run producing CSV artifacts.

### Phase 2 — operational analytics

- DuckDB/SQL reconciliation views;
- merchant/day exception summaries;
- aging and exposure reporting;
- demo notebook;
- management-level output tables/charts.

### Phase 3 — optional intelligence layer

Only after Phase 1 is frozen:

- merchant-level anomaly detection;
- unusual refund/fee/settlement patterns;
- ranked investigation queues;
- comparison of deterministic rules vs anomaly-assisted prioritization.

## 12. V1 success criteria

V1 is considered complete when a clean checkout can:

1. install dependencies;
2. generate the same synthetic dataset from the same seed;
3. run reconciliation without reading the truth file;
4. emit a normalized exception queue;
5. evaluate the queue against the hidden-to-engine truth file;
6. pass automated tests;
7. reproduce headline metrics from saved artifacts;
8. explain every detection rule and every financial-exposure calculation in the README/docs.

## 13. Design principle

The project should optimize for auditability over novelty. A transparent reconciliation rule that can be tested and explained is preferable to an opaque model that produces a higher-looking score without a credible operational interpretation.
