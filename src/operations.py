"""Truth-blind, event-time operational warehouse. SQL owns matching and metrics."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src.reconciliation import ReconciliationConfig

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
# Only these columns can cross the warehouse boundary, even if callers supply labels.
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
REPORTS = (
    "reconciliation_facts", "detected_exceptions", "investigation_queue", "summary",
    "merchant_metrics", "daily_metrics", "payment_method_metrics", "gateway_metrics",
    "exception_type_metrics", "aging_metrics", "settlement_delay_metrics", "daily_settlements",
)


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
        # Existing V1 feed has no provider field; never fabricate provider attribution.
        result["gateway_id"] = frame["gateway_id"].fillna("unknown").astype(str) if "gateway_id" in frame else "unknown"
        result.loc[result.gateway_id.str.strip().eq(""), "gateway_id"] = "unknown"
    return result


@dataclass
class OperationalResult:
    tables: dict[str, pd.DataFrame]
    metadata: dict


def run_operations(internal: pd.DataFrame, gateway: pd.DataFrame, settlement: pd.DataFrame,
                   *, as_of, config: ReconciliationConfig = ReconciliationConfig()) -> OperationalResult:
    """Reconcile only operational feeds; no generation or evaluation dependency.

    as_of is inclusive and mandatory. Timestamps are normalized to UTC; naive input
    is interpreted as UTC. Event time is not a substitute for ingestion-time history.
    """
    cutoff = timestamp(as_of)
    for key, value in asdict(config).items():
        if not np.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be finite and nonnegative")
    frames = {name: _prepare(df, name) for name, df in zip(CONTRACTS, (internal, gateway, settlement))}
    source_counts = {}
    with duckdb.connect(":memory:") as con:
        con.execute("SET threads=1")
        con.execute("CREATE TABLE run_config AS SELECT ?::TIMESTAMP AS as_of, ?::DECIMAL(18,6) AS amount_tolerance, ?::DECIMAL(18,6) AS fee_tolerance, ?::DOUBLE AS max_settlement_days",
                    [cutoff.to_pydatetime(), config.amount_tolerance, config.fee_tolerance, config.max_settlement_days])
        for name, frame in frames.items():
            contract = CONTRACTS[name]
            con.register("incoming", frame)
            expressions = [f'CAST("{col}" AS DECIMAL(18,2)) AS "{col}"' if col in contract["money"] else (f'CAST("{col}" AS TIMESTAMP) AS "{col}"' if col == contract["time"] else f'CAST("{col}" AS VARCHAR) AS "{col}"') for col in frame.columns]
            con.execute(f'CREATE TABLE {name} AS SELECT {", ".join(expressions)} FROM incoming WHERE "{contract["time"]}" <= (SELECT as_of FROM run_config)')
            included = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            source_counts[name] = {"input_rows": len(frame), "included_rows": included, "future_rows_excluded": len(frame) - included}
            con.unregister("incoming")
        for filename in ("01_reconciliation.sql", "02_exceptions.sql", "03_analytics.sql"):
            con.execute((SQL_DIR / filename).read_text(encoding="utf-8"))
        tables = {name: con.execute(f"SELECT * FROM {name} ORDER BY ALL").df() for name in REPORTS}
        tables["investigation_queue"] = tables["investigation_queue"].sort_values("priority_rank", ignore_index=True)
    return OperationalResult(tables, {
        "schema_version": "phase2-v1", "as_of_utc": cutoff.isoformat(), "currency": "INR",
        "config": asdict(config), "source_counts": source_counts,
        "benchmark_claim": "Controlled synthetic demonstration; not real-world accuracy or realized loss.",
    })
