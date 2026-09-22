# Five-minute operations walkthrough

This is a synthetic demonstration of how to turn three payment exports into a review queue. It does not connect to a live payment system or recover money.

## 1. Explain the business question

An internal ledger says what a business intended to collect. A gateway export says what the processor recorded. A bank settlement export says what arrived, after fees and tax. The project matches transaction IDs across these sources and identifies differences that need investigation.

Use the [README quick start](../README.md#quick-start) to generate the reference data and open `artifacts/phase2/management_report.html`. No server is required.

## 2. Show four actual examples from the reference run

These rows come from seed 42, 10,000 intended payments and the cutoff `2026-02-10T00:00:00Z`. Find them in `reconciliation_facts.csv` and `investigation_queue.csv` beside the HTML report.

| Transaction | What the sources show | What the queue means |
|---|---|---|
| `T0000249` | Gateway expects ₹5,718.13 net; no bank line exists after the settlement window. | ₹5,718.13 of overdue expected cash. Request a settlement trace; the missing line alone does not prove lost money. |
| `T0000109` | Gateway net is ₹461.92; bank net is ₹459.92. A fee difference also triggers a net mismatch. | One case, two signals, ₹2.00 of estimated discrepancy. Adding both rule amounts would count the same difference twice. |
| `T0000057` | Multiple gateway records share one payment ID. The duplicate gross excess is ₹1,806.87. | Investigate repeated feed delivery versus duplicate capture. A duplicated row does not prove that money moved twice. |
| `T0000250` | ₹1,333.06 net eventually arrived, after the settlement window. | Historical late cash of ₹1,333.06 and zero current outstanding exposure for this case. |

## 3. Show what the system controls

- **Timing:** move the cutoff earlier in the notebook. Future bank receipts disappear from that snapshot; payments can be pending or overdue.
- **Double counting:** duplicate feeds are aggregated before joining. One payment can trigger several rules but contributes one queue row.
- **Ownership of the next action:** the queue includes a suggested team/action, severity, age and supporting rule names. It does not claim that anyone has accepted or completed the case.
- **Auditability:** adjacent CSVs contain the full results. `manifest.json` records inputs, code hashes, configuration, versions and output checksums.

## 4. State the evidence accurately

The controlled V1 benchmark deliberately injects 300 exceptions; its matching rules detect all 300 with zero false positives. This demonstrates correctness on the constructed cases, not real-world accuracy. The two additional SQL categories are checked separately with hand-calculated tests because the V1 truth set does not label them independently.

The reference report's ₹281,432.26 is an investigation estimate: ₹215,440.49 of financial discrepancies plus ₹65,991.77 of overdue expected cash. It is not recovered cash, verified loss or a savings claim. Historical late cash is excluded.

## 5. Explain what client adaptation would require

A real engagement would begin by checking sample exports, ID consistency, settlement timing and fee contracts with the finance owner. This implementation assumes INR, exact ID matching and a simplified payment lifecycle. Refunds, split settlements, chargebacks, ingestion history and persistent case resolution are outside its scope. An existing reconciliation product may already meet the client's needs.

The capability demonstrated here is a traceable SQL and financial-controls workflow. The separate [fraud experiment](https://github.com/aaravb015/transaction-fraud-intelligence) demonstrates model comparison and behavioural risk analysis.
