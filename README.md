# Payments reconciliation

A small Python and SQL project to match payments from an internal ledger, a payment gateway and a bank settlement file.

It flags missing or duplicate records, unmatched payments, amount and status differences, incorrect fees and late settlements. The output includes an exception list and a ranked investigation queue.

## Run

```bash
pip install -r requirements.txt
python reconcile.py --as-of 2026-02-10
```

This generates 10,000 synthetic payments and reconciles them. Results and sample input CSVs are saved in `outputs/`.

To use existing INR payment files with the same columns:

```bash
python reconcile.py --input-dir data --as-of 2026-02-10
```

The reporting cutoff is in UTC. A successful payment is overdue only after three days without a settlement. Duplicate rows are counted before joining to avoid multiplying amounts.

## Results

Original reference run (seed 42, 10,000 payments):

| Metric | Result |
| --- | ---: |
| Injected exceptions | 300 |
| Original 10-rule benchmark detections | 300 |
| False positives in original benchmark | 0 |
| Transactions flagged by operational rules | 300 |
| Total rule signals | 330 |

The extra 30 signals come from overlapping bank net and fee differences. The investigation queue combines overlapping signals into one case per transaction. Current estimated exposure and historical late cash are reported separately.

These are controlled synthetic results, not real-world accuracy, losses or recovered funds. The original [implementation and reports](https://github.com/aaravb015/payments-reconciliation-intelligence/tree/4c46fd448accaf7845b30a4947b7e4f390ca45c4) are available in Git history. The simplified version has not been independently benchmarked yet.

## Files

- `generate_data.py` creates the three feeds and separate evaluation labels.
- `reconciliation.sql` contains the matching and exception rules.
- `reconcile.py` runs the SQL and exports the results.

Run tests with `python -m pytest -q`.
