"""Read exactly three operational CSVs; the reporting process never opens truth."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

# Support the documented direct-script command from a clean checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.operations import run_operations
from src.reconciliation import ReconciliationConfig
from src.reporting import write_reports

SOURCE_FILES = {
    'internal': 'internal_payments.csv',
    'gateway': 'gateway_transactions.csv',
    'settlement': 'bank_settlements.csv',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True)
    parser.add_argument('--as-of', required=True, help='Inclusive UTC cutoff; naive timestamps are interpreted as UTC')
    parser.add_argument('--output-dir', default='artifacts/phase2')
    parser.add_argument('--max-settlement-days', type=float, default=3)
    parser.add_argument('--amount-tolerance', type=float, default=0.05)
    parser.add_argument('--fee-tolerance', type=float, default=0.05)
    args = parser.parse_args()
    paths = {k: Path(args.input_dir) / v for k, v in SOURCE_FILES.items()}
    # String dtype preserves leading zeroes in identifiers when reading CSV.
    sources = {k: pd.read_csv(p, dtype=str) for k, p in paths.items()}
    result = run_operations(**sources, as_of=args.as_of, config=ReconciliationConfig(
        amount_tolerance=args.amount_tolerance, fee_tolerance=args.fee_tolerance,
        max_settlement_days=args.max_settlement_days))
    report = write_reports(result, args.output_dir, source_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths.values()})
    print(result.tables['summary'].to_string(index=False))
    print(f'Management report: {report}')


if __name__ == '__main__':
    main()
