from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd


FEE_RATES: Dict[str, float] = {
    "card": 0.0200,
    "upi": 0.0020,
    "net_banking": 0.0125,
    "wallet": 0.0150,
}
TAX_RATE = 0.18


@dataclass(frozen=True)
class GenerationConfig:
    seed: int = 42
    n_transactions: int = 10_000
    n_merchants: int = 20
    n_customers: int = 2_000
    start_date: str = "2026-01-01"
    n_days: int = 30
    exception_rate: float = 0.03


def _money(values) -> np.ndarray:
    return np.round(np.asarray(values, dtype=float), 2)


def _recalculate_gateway_financials(df: pd.DataFrame, mask: pd.Series) -> None:
    df.loc[mask, "gateway_fee"] = _money(
        df.loc[mask, "gateway_amount"] * df.loc[mask, "fee_rate"]
    )
    df.loc[mask, "gateway_tax"] = _money(
        df.loc[mask, "gateway_fee"] * TAX_RATE
    )
    df.loc[mask, "gateway_net"] = _money(
        df.loc[mask, "gateway_amount"]
        - df.loc[mask, "gateway_fee"]
        - df.loc[mask, "gateway_tax"]
    )


def generate_data(config: GenerationConfig = GenerationConfig()) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate synthetic internal, gateway, settlement, and truth datasets.

    The truth dataframe is produced for evaluation only. Reconciliation code should
    never require it as an input.
    """
    rng = np.random.default_rng(config.seed)
    n = config.n_transactions
    start = pd.Timestamp(config.start_date)

    tx_ids = np.array([f"T{i:07d}" for i in range(1, n + 1)])
    order_ids = np.array([f"O{i:07d}" for i in range(1, n + 1)])
    merchant_ids = np.array([f"M{i:03d}" for i in range(1, config.n_merchants + 1)])
    customer_ids = np.array([f"C{i:05d}" for i in range(1, config.n_customers + 1)])

    day_offsets = rng.integers(0, config.n_days, size=n)
    second_offsets = rng.integers(0, 24 * 60 * 60, size=n)
    created_at = start + pd.to_timedelta(day_offsets, unit="D") + pd.to_timedelta(second_offsets, unit="s")

    payment_methods = rng.choice(
        ["card", "upi", "net_banking", "wallet"],
        size=n,
        p=[0.42, 0.38, 0.12, 0.08],
    )
    amounts = _money(np.clip(rng.lognormal(mean=7.2, sigma=0.85, size=n), 100, 25_000))

    internal = pd.DataFrame(
        {
            "transaction_id": tx_ids,
            "order_id": order_ids,
            "merchant_id": rng.choice(merchant_ids, size=n),
            "customer_id": rng.choice(customer_ids, size=n),
            "created_at": pd.to_datetime(created_at),
            "currency": "INR",
            "payment_method": payment_methods,
            "internal_status": "success",
            "gross_amount": amounts,
        }
    ).sort_values(["created_at", "transaction_id"], ignore_index=True)

    gateway = internal[
        ["transaction_id", "order_id", "created_at", "payment_method", "gross_amount"]
    ].copy()
    gateway.insert(0, "gateway_record_id", [f"G{i:07d}" for i in range(1, n + 1)])
    gateway["gateway_event_at"] = gateway["created_at"] + pd.to_timedelta(
        rng.integers(1, 11, size=n), unit="m"
    )
    gateway["gateway_status"] = "success"
    gateway["gateway_amount"] = gateway["gross_amount"]
    gateway["fee_rate"] = gateway["payment_method"].map(FEE_RATES).astype(float)
    gateway["gateway_fee"] = _money(gateway["gateway_amount"] * gateway["fee_rate"])
    gateway["gateway_tax"] = _money(gateway["gateway_fee"] * TAX_RATE)
    gateway["gateway_net"] = _money(
        gateway["gateway_amount"] - gateway["gateway_fee"] - gateway["gateway_tax"]
    )
    gateway = gateway[
        [
            "gateway_record_id",
            "transaction_id",
            "order_id",
            "gateway_event_at",
            "gateway_status",
            "gateway_amount",
            "fee_rate",
            "gateway_fee",
            "gateway_tax",
            "gateway_net",
        ]
    ]

    settle_days = rng.choice([1, 2], size=n, p=[0.72, 0.28])
    settlement = gateway[
        [
            "transaction_id",
            "gateway_event_at",
            "gateway_amount",
            "gateway_fee",
            "gateway_tax",
            "gateway_net",
        ]
    ].copy()
    settlement.insert(0, "settlement_line_id", [f"S{i:07d}" for i in range(1, n + 1)])
    settlement["settled_at"] = (
        settlement["gateway_event_at"]
        + pd.to_timedelta(settle_days, unit="D")
        + pd.to_timedelta(rng.integers(0, 6 * 60, size=n), unit="m")
    )
    settlement["settlement_batch_id"] = (
        "B" + settlement["settled_at"].dt.strftime("%Y%m%d")
    )
    settlement = settlement.rename(
        columns={
            "gateway_amount": "gross_amount",
            "gateway_fee": "fee_amount",
            "gateway_tax": "tax_amount",
            "gateway_net": "net_amount",
        }
    )
    settlement = settlement[
        [
            "settlement_line_id",
            "settlement_batch_id",
            "transaction_id",
            "settled_at",
            "gross_amount",
            "fee_amount",
            "tax_amount",
            "net_amount",
        ]
    ]

    exception_types = [
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
    ]
    total_exceptions = max(len(exception_types), int(round(n * config.exception_rate)))
    per_type = total_exceptions // len(exception_types)
    remainder = total_exceptions % len(exception_types)
    counts = {
        exc: per_type + (1 if i < remainder else 0)
        for i, exc in enumerate(exception_types)
    }

    in_universe_types = exception_types[:8]
    needed = sum(counts[e] for e in in_universe_types)
    if needed >= n:
        raise ValueError("Exception configuration leaves insufficient clean transactions.")
    chosen_positions = rng.choice(np.arange(n), size=needed, replace=False)

    truth_rows = []
    truth_counter = 1
    cursor = 0

    def add_truth(tx_id: str, exception_type: str, source: str, reference_amount: float, notes: str) -> None:
        nonlocal truth_counter
        truth_rows.append(
            {
                "truth_exception_id": f"X{truth_counter:06d}",
                "transaction_id": tx_id,
                "exception_type": exception_type,
                "injected_source": source,
                "reference_amount": round(float(reference_amount), 2),
                "notes": notes,
            }
        )
        truth_counter += 1

    selected_by_type = {}
    for exc in in_universe_types:
        c = counts[exc]
        positions = chosen_positions[cursor : cursor + c]
        cursor += c
        selected_by_type[exc] = internal.iloc[positions]["transaction_id"].tolist()

    ids = selected_by_type["missing_gateway"]
    for tx_id in ids:
        amt = internal.loc[internal["transaction_id"].eq(tx_id), "gross_amount"].iloc[0]
        add_truth(tx_id, "missing_gateway", "gateway", amt, "Gateway record removed.")
    gateway = gateway.loc[~gateway["transaction_id"].isin(ids)].copy()

    ids = selected_by_type["missing_settlement"]
    for tx_id in ids:
        net = settlement.loc[settlement["transaction_id"].eq(tx_id), "net_amount"].iloc[0]
        add_truth(tx_id, "missing_settlement", "settlement", net, "Settlement line removed.")
    settlement = settlement.loc[~settlement["transaction_id"].isin(ids)].copy()

    ids = selected_by_type["amount_mismatch"]
    for tx_id in ids:
        base_amt = internal.loc[internal["transaction_id"].eq(tx_id), "gross_amount"].iloc[0]
        delta = max(5.0, round(float(base_amt) * float(rng.uniform(0.02, 0.08)), 2))
        gmask = gateway["transaction_id"].eq(tx_id)
        gateway.loc[gmask, "gateway_amount"] = _money(gateway.loc[gmask, "gateway_amount"] + delta)
        _recalculate_gateway_financials(gateway, gmask)
        smask = settlement["transaction_id"].eq(tx_id)
        settlement.loc[smask, "gross_amount"] = gateway.loc[gmask, "gateway_amount"].iloc[0]
        settlement.loc[smask, "fee_amount"] = gateway.loc[gmask, "gateway_fee"].iloc[0]
        settlement.loc[smask, "tax_amount"] = gateway.loc[gmask, "gateway_tax"].iloc[0]
        settlement.loc[smask, "net_amount"] = gateway.loc[gmask, "gateway_net"].iloc[0]
        add_truth(tx_id, "amount_mismatch", "gateway", delta, "Gateway amount increased from internal amount.")

    ids = selected_by_type["status_mismatch"]
    for tx_id in ids:
        gmask = gateway["transaction_id"].eq(tx_id)
        gateway.loc[gmask, "gateway_status"] = "failed"
        amt = internal.loc[internal["transaction_id"].eq(tx_id), "gross_amount"].iloc[0]
        add_truth(tx_id, "status_mismatch", "gateway", amt, "Gateway status changed to failed.")

    ids = selected_by_type["duplicate_gateway"]
    duplicate_gateway_rows = []
    next_g = n + 1
    for tx_id in ids:
        row = gateway.loc[gateway["transaction_id"].eq(tx_id)].iloc[0].copy()
        row["gateway_record_id"] = f"G{next_g:07d}"
        next_g += 1
        duplicate_gateway_rows.append(row)
        add_truth(tx_id, "duplicate_gateway", "gateway", row["gateway_amount"], "Second gateway row added.")
    if duplicate_gateway_rows:
        gateway = pd.concat([gateway, pd.DataFrame(duplicate_gateway_rows)], ignore_index=True)

    ids = selected_by_type["duplicate_settlement"]
    duplicate_settlement_rows = []
    next_s = n + 1
    for tx_id in ids:
        row = settlement.loc[settlement["transaction_id"].eq(tx_id)].iloc[0].copy()
        row["settlement_line_id"] = f"S{next_s:07d}"
        next_s += 1
        duplicate_settlement_rows.append(row)
        add_truth(tx_id, "duplicate_settlement", "settlement", row["net_amount"], "Second settlement line added.")
    if duplicate_settlement_rows:
        settlement = pd.concat([settlement, pd.DataFrame(duplicate_settlement_rows)], ignore_index=True)

    ids = selected_by_type["fee_mismatch"]
    for tx_id in ids:
        smask = settlement["transaction_id"].eq(tx_id)
        base_fee = float(settlement.loc[smask, "fee_amount"].iloc[0])
        extra = max(2.0, round(base_fee * float(rng.uniform(0.15, 0.35)), 2))
        settlement.loc[smask, "fee_amount"] = _money(settlement.loc[smask, "fee_amount"] + extra)
        settlement.loc[smask, "net_amount"] = _money(
            settlement.loc[smask, "gross_amount"]
            - settlement.loc[smask, "fee_amount"]
            - settlement.loc[smask, "tax_amount"]
        )
        add_truth(tx_id, "fee_mismatch", "settlement", extra, "Settlement fee increased above gateway fee.")

    ids = selected_by_type["settlement_delay"]
    for tx_id in ids:
        smask = settlement["transaction_id"].eq(tx_id)
        gtime = gateway.loc[gateway["transaction_id"].eq(tx_id), "gateway_event_at"].iloc[0]
        settlement.loc[smask, "settled_at"] = pd.Timestamp(gtime) + pd.Timedelta(days=7)
        settlement.loc[smask, "settlement_batch_id"] = "B" + pd.Timestamp(
            settlement.loc[smask, "settled_at"].iloc[0]
        ).strftime("%Y%m%d")
        net = settlement.loc[smask, "net_amount"].iloc[0]
        add_truth(tx_id, "settlement_delay", "settlement", net, "Settlement moved to T+7.")

    orphan_gateway_rows = []
    for i in range(counts["orphan_gateway"]):
        tx_id = f"OG{i+1:06d}"
        amount = round(float(np.clip(rng.lognormal(mean=7.1, sigma=0.75), 100, 25_000)), 2)
        method = rng.choice(list(FEE_RATES))
        fee_rate = FEE_RATES[method]
        fee = round(amount * fee_rate, 2)
        tax = round(fee * TAX_RATE, 2)
        net = round(amount - fee - tax, 2)
        event_at = start + pd.Timedelta(days=int(rng.integers(0, config.n_days))) + pd.Timedelta(
            seconds=int(rng.integers(0, 24 * 60 * 60))
        )
        orphan_gateway_rows.append(
            {
                "gateway_record_id": f"G{next_g:07d}",
                "transaction_id": tx_id,
                "order_id": f"OO{i+1:06d}",
                "gateway_event_at": event_at,
                "gateway_status": "success",
                "gateway_amount": amount,
                "fee_rate": fee_rate,
                "gateway_fee": fee,
                "gateway_tax": tax,
                "gateway_net": net,
            }
        )
        next_g += 1
        add_truth(tx_id, "orphan_gateway", "gateway", amount, "Gateway-only transaction inserted.")
    if orphan_gateway_rows:
        gateway = pd.concat([gateway, pd.DataFrame(orphan_gateway_rows)], ignore_index=True)

    orphan_settlement_rows = []
    for i in range(counts["orphan_settlement"]):
        tx_id = f"OS{i+1:06d}"
        amount = round(float(np.clip(rng.lognormal(mean=7.1, sigma=0.75), 100, 25_000)), 2)
        fee = round(amount * 0.015, 2)
        tax = round(fee * TAX_RATE, 2)
        net = round(amount - fee - tax, 2)
        settled_at = start + pd.Timedelta(days=int(rng.integers(1, config.n_days + 2)))
        orphan_settlement_rows.append(
            {
                "settlement_line_id": f"S{next_s:07d}",
                "settlement_batch_id": "B" + settled_at.strftime("%Y%m%d"),
                "transaction_id": tx_id,
                "settled_at": settled_at,
                "gross_amount": amount,
                "fee_amount": fee,
                "tax_amount": tax,
                "net_amount": net,
            }
        )
        next_s += 1
        add_truth(tx_id, "orphan_settlement", "settlement", net, "Settlement-only transaction inserted.")
    if orphan_settlement_rows:
        settlement = pd.concat([settlement, pd.DataFrame(orphan_settlement_rows)], ignore_index=True)

    truth = pd.DataFrame(truth_rows).sort_values(
        ["exception_type", "transaction_id"], ignore_index=True
    )

    internal = internal.sort_values(["created_at", "transaction_id"], ignore_index=True)
    gateway = gateway.sort_values(["gateway_event_at", "transaction_id", "gateway_record_id"], ignore_index=True)
    settlement = settlement.sort_values(["settled_at", "transaction_id", "settlement_line_id"], ignore_index=True)

    return internal, gateway, settlement, truth


def write_dataset(output_dir: str | Path, config: GenerationConfig = GenerationConfig()) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    internal, gateway, settlement, truth = generate_data(config)
    paths = {
        "internal": output / "internal_payments.csv",
        "gateway": output / "gateway_transactions.csv",
        "settlement": output / "bank_settlements.csv",
        "truth": output / "truth_exceptions.csv",
    }
    internal.to_csv(paths["internal"], index=False)
    gateway.to_csv(paths["gateway"], index=False)
    settlement.to_csv(paths["settlement"], index=False)
    truth.to_csv(paths["truth"], index=False)
    return paths
