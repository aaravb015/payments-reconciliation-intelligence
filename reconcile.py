from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


SQL_FILE = Path(__file__).with_name("reconciliation.sql")
CONTRACTS = {
    "internal": {
        "keys": ["transaction_id", "order_id", "merchant_id", "payment_method", "currency", "internal_status"],
        "time": "created_at", "money": ["gross_amount"],
    },
    "gateway": {
        "keys": ["transaction_id", "gateway_record_id", "gateway_status"],
        "time": "gateway_event_at", "money": ["gateway_amount", "gateway_fee", "gateway_tax", "gateway_net"],
    },
    "settlement": {
        "keys": ["transaction_id", "settlement_line_id"],
        "time": "settled_at", "money": ["gross_amount", "fee_amount", "tax_amount", "net_amount"],
    },
}


def timestamp(value) -> pd.Timestamp:
    result = pd.to_datetime(value, utc=True, errors="raise")
    if pd.isna(result):
        raise ValueError("as_of must be a valid explicit timestamp")
    return result.tz_convert(None)


def _prepare(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    contract = CONTRACTS[source]
    columns = contract["keys"] + [contract["time"]] + contract["money"]
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{source} missing columns: {sorted(missing)}")
    result = frame[columns].copy()
    for col in contract["keys"]:
        if result[col].isna().any() or result[col].astype(str).str.strip().eq("").any():
            raise ValueError(f"{source}.{col} contains missing/blank values")
        result[col] = result[col].astype(str)
    result[contract["time"]] = pd.to_datetime(result[contract["time"]], utc=True, errors="raise").dt.tz_convert(None)
    if result[contract["time"]].isna().any():
        raise ValueError(f"{source} has missing timestamps")
    for col in contract["money"]:
        values = pd.to_numeric(result[col], errors="raise")
        if (not np.isfinite(values).all() or values.lt(0).any()
                or (values * 100 - (values * 100).round()).abs().gt(0.00001).any()
                or values.ge(10**13).any()):
            raise ValueError(f"{source}.{col} must be finite, nonnegative INR with at most two decimal places")
        result[col] = values.astype(float)
    if source == "internal":
        if result.transaction_id.duplicated().any():
            raise ValueError("Internal transaction_id must be unique")
        if not result.currency.eq("INR").all():
            raise ValueError("Only INR is supported; currency conversion is not implemented")
        result.internal_status = result.internal_status.str.lower()
    if source == "gateway":
        result.gateway_status = result.gateway_status.str.lower()
        result["gateway_id"] = frame["gateway_id"].fillna("unknown").astype(str) if "gateway_id" in frame else "unknown"
        result.loc[result.gateway_id.str.strip().eq(""), "gateway_id"] = "unknown"
    return result


def reconcile(internal, gateway, settlement, as_of):
    cutoff = timestamp(as_of)
    frames = {
        name: _prepare(frame, name)
        for name, frame in zip(CONTRACTS, (internal, gateway, settlement))
    }
    with duckdb.connect(":memory:") as con:
        con.execute("SET threads=1")
        con.execute("""
            CREATE TABLE run_config AS
            SELECT ?::TIMESTAMP AS as_of, 0.05::DECIMAL(18,6) AS amount_tolerance,
                   0.05::DECIMAL(18,6) AS fee_tolerance, 3.0 AS max_settlement_days
        """, [cutoff.to_pydatetime()])

        for name, frame in frames.items():
            contract = CONTRACTS[name]
            frame = frame.loc[frame[contract["time"]] <= cutoff]
            con.register("incoming", frame)
            columns = []
            for col in frame.columns:
                sql_type = "DECIMAL(18,2)" if col in contract["money"] else (
                    "TIMESTAMP" if col == contract["time"] else "VARCHAR")
                columns.append(f'CAST("{col}" AS {sql_type}) AS "{col}"')
            con.execute(f'CREATE TABLE {name} AS SELECT {", ".join(columns)} FROM incoming')
            con.unregister("incoming")

        con.execute(SQL_FILE.read_text())
        facts = con.execute("SELECT * FROM reconciliation_facts ORDER BY transaction_id").df()
        exceptions = con.execute("SELECT * FROM detected_exceptions ORDER BY transaction_id, exception_type").df()
        queue = con.execute("SELECT * FROM investigation_queue ORDER BY priority_rank").df()

    summary = pd.DataFrame([{
        "observed_transactions": len(facts),
        "exception_transactions": len(queue),
        "exception_count": len(exceptions),
        "pending_payments": int(facts.settlement_state.eq("pending").sum()),
        "overdue_payments": int(facts.settlement_state.eq("overdue").sum()),
        "financial_exposure_inr": round(float(queue.financial_exposure_inr.sum()), 2),
        "overdue_cash_inr": round(float(queue.overdue_cash_inr.sum()), 2),
        "current_exposure_inr": round(float(queue.current_exposure_inr.sum()), 2),
        "historical_late_cash_inr": round(float(queue.historical_late_cash_inr.sum()), 2),
    }])
    return {"reconciliation_facts": facts, "detected_exceptions": exceptions,
            "investigation_queue": queue, "summary": summary}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", default="outputs")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    names = ("internal_payments", "gateway_transactions", "bank_settlements")
    if args.input_dir:
        feeds = [pd.read_csv(Path(args.input_dir) / f"{name}.csv",
                             dtype={"transaction_id": str}) for name in names]
    else:
        from generate_data import generate_data
        *feeds, _ = generate_data()
        for name, frame in zip(names, feeds):
            frame.to_csv(out / f"{name}.csv", index=False)

    results = reconcile(*feeds, as_of=args.as_of)
    for name, frame in results.items():
        frame.to_csv(out / f"{name}.csv", index=False)
    print(results["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
