from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ReconciliationConfig:
    amount_tolerance: float = 0.05
    fee_tolerance: float = 0.05
    max_settlement_days: float = 3.0


_INTERNAL_REQUIRED = {
    "transaction_id",
    "order_id",
    "created_at",
    "internal_status",
    "gross_amount",
}
_GATEWAY_REQUIRED = {
    "gateway_record_id",
    "transaction_id",
    "gateway_event_at",
    "gateway_status",
    "gateway_amount",
    "gateway_fee",
    "gateway_tax",
    "gateway_net",
}
_SETTLEMENT_REQUIRED = {
    "settlement_line_id",
    "transaction_id",
    "settled_at",
    "gross_amount",
    "fee_amount",
    "tax_amount",
    "net_amount",
}


def _validate(df: pd.DataFrame, required: set[str], source: str) -> None:
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"{source} is missing required columns: {sorted(missing)}")
    if df["transaction_id"].isna().any():
        raise ValueError(f"{source}.transaction_id contains null values.")


def _exception(
    exception_type: str,
    transaction_id: str,
    severity: str,
    amount_at_risk: float,
    exposure_kind: str,
    reason: str,
) -> dict:
    return {
        "exception_type": exception_type,
        "transaction_id": str(transaction_id),
        "severity": severity,
        "amount_at_risk": round(float(max(amount_at_risk, 0.0)), 2),
        "exposure_kind": exposure_kind,
        "reason": reason,
    }


def reconcile(
    internal: pd.DataFrame,
    gateway: pd.DataFrame,
    settlement: pd.DataFrame,
    config: ReconciliationConfig = ReconciliationConfig(),
) -> pd.DataFrame:
    """Detect reconciliation exceptions without any ground-truth labels."""
    _validate(internal, _INTERNAL_REQUIRED, "internal")
    _validate(gateway, _GATEWAY_REQUIRED, "gateway")
    _validate(settlement, _SETTLEMENT_REQUIRED, "settlement")

    internal = internal.copy()
    gateway = gateway.copy()
    settlement = settlement.copy()

    internal["created_at"] = pd.to_datetime(internal["created_at"])
    gateway["gateway_event_at"] = pd.to_datetime(gateway["gateway_event_at"])
    settlement["settled_at"] = pd.to_datetime(settlement["settled_at"])

    if internal["transaction_id"].duplicated().any():
        dupes = internal.loc[internal["transaction_id"].duplicated(keep=False), "transaction_id"].unique()
        raise ValueError(
            "Internal ledger must contain one intended payment per transaction_id; "
            f"duplicates found for {len(dupes)} transaction(s)."
        )

    rows: list[dict] = []

    g_counts = gateway["transaction_id"].value_counts()
    s_counts = settlement["transaction_id"].value_counts()
    duplicate_g_ids = set(g_counts[g_counts > 1].index.astype(str))
    duplicate_s_ids = set(s_counts[s_counts > 1].index.astype(str))

    for tx_id in sorted(duplicate_g_ids):
        subset = gateway.loc[gateway["transaction_id"].astype(str).eq(tx_id)]
        amount = float(subset["gateway_amount"].max())
        rows.append(
            _exception(
                "duplicate_gateway",
                tx_id,
                "critical",
                amount,
                "potential_loss",
                f"{len(subset)} gateway records share the same transaction_id.",
            )
        )

    for tx_id in sorted(duplicate_s_ids):
        subset = settlement.loc[settlement["transaction_id"].astype(str).eq(tx_id)]
        amount = float(subset["net_amount"].max())
        rows.append(
            _exception(
                "duplicate_settlement",
                tx_id,
                "critical",
                amount,
                "potential_loss",
                f"{len(subset)} settlement lines share the same transaction_id.",
            )
        )

    gateway_unique = gateway.loc[~gateway["transaction_id"].astype(str).isin(duplicate_g_ids)].copy()
    settlement_unique = settlement.loc[~settlement["transaction_id"].astype(str).isin(duplicate_s_ids)].copy()

    internal_ids = set(internal["transaction_id"].astype(str))
    gateway_ids = set(gateway["transaction_id"].astype(str))
    settlement_ids = set(settlement["transaction_id"].astype(str))

    missing_gateway = internal.loc[~internal["transaction_id"].astype(str).isin(gateway_ids)]
    for row in missing_gateway.itertuples(index=False):
        rows.append(
            _exception(
                "missing_gateway",
                row.transaction_id,
                "high",
                row.gross_amount,
                "potential_loss",
                "Internal payment exists but no gateway record was found.",
            )
        )

    orphan_gateway = gateway_unique.loc[~gateway_unique["transaction_id"].astype(str).isin(internal_ids)]
    for row in orphan_gateway.itertuples(index=False):
        rows.append(
            _exception(
                "orphan_gateway",
                row.transaction_id,
                "high",
                row.gateway_amount,
                "potential_loss",
                "Gateway transaction has no matching internal payment.",
            )
        )

    orphan_settlement = settlement_unique.loc[
        ~settlement_unique["transaction_id"].astype(str).isin(internal_ids)
    ]
    for row in orphan_settlement.itertuples(index=False):
        rows.append(
            _exception(
                "orphan_settlement",
                row.transaction_id,
                "critical",
                row.net_amount,
                "potential_loss",
                "Settlement line has no matching internal payment.",
            )
        )

    ig = internal.merge(
        gateway_unique,
        on="transaction_id",
        how="inner",
        suffixes=("_internal", "_gateway"),
        validate="one_to_one",
    )

    amount_delta = (ig["gross_amount"] - ig["gateway_amount"]).abs()
    for idx in ig.index[amount_delta > config.amount_tolerance]:
        row = ig.loc[idx]
        delta = float(abs(row["gross_amount"] - row["gateway_amount"]))
        rows.append(
            _exception(
                "amount_mismatch",
                row["transaction_id"],
                "high",
                delta,
                "potential_loss",
                f"Internal gross {row['gross_amount']:.2f} differs from gateway amount {row['gateway_amount']:.2f}.",
            )
        )

    status_mismatch = ig["internal_status"].astype(str).str.lower() != ig["gateway_status"].astype(str).str.lower()
    for idx in ig.index[status_mismatch]:
        row = ig.loc[idx]
        rows.append(
            _exception(
                "status_mismatch",
                row["transaction_id"],
                "high",
                row["gross_amount"],
                "data_integrity",
                f"Internal status {row['internal_status']} differs from gateway status {row['gateway_status']}.",
            )
        )

    eligible = ig.loc[
        ig["internal_status"].astype(str).str.lower().eq("success")
        & ig["gateway_status"].astype(str).str.lower().eq("success")
    ].copy()
    eligible = eligible.loc[~eligible["transaction_id"].astype(str).isin(duplicate_s_ids)]
    missing_settlement = eligible.loc[
        ~eligible["transaction_id"].astype(str).isin(settlement_ids)
    ]
    for row in missing_settlement.itertuples(index=False):
        rows.append(
            _exception(
                "missing_settlement",
                row.transaction_id,
                "critical",
                row.gateway_net,
                "potential_loss",
                "Successful payment has no bank settlement line.",
            )
        )

    gs = gateway_unique.merge(
        settlement_unique,
        on="transaction_id",
        how="inner",
        suffixes=("_gateway", "_settlement"),
        validate="one_to_one",
    )

    gs_success = gs.loc[gs["gateway_status"].astype(str).str.lower().eq("success")].copy()

    fee_delta = (
        (gs_success["gateway_fee"] - gs_success["fee_amount"]).abs()
        + (gs_success["gateway_tax"] - gs_success["tax_amount"]).abs()
    )
    for idx in gs_success.index[fee_delta > config.fee_tolerance]:
        row = gs_success.loc[idx]
        delta = float(
            abs(row["gateway_fee"] - row["fee_amount"])
            + abs(row["gateway_tax"] - row["tax_amount"])
        )
        rows.append(
            _exception(
                "fee_mismatch",
                row["transaction_id"],
                "medium",
                delta,
                "potential_loss",
                "Bank-reported fee/tax differs from gateway fee/tax.",
            )
        )

    settlement_age_days = (
        gs_success["settled_at"] - gs_success["gateway_event_at"]
    ).dt.total_seconds() / 86400.0
    for idx in gs_success.index[settlement_age_days > config.max_settlement_days]:
        row = gs_success.loc[idx]
        age = float(
            (row["settled_at"] - row["gateway_event_at"]).total_seconds() / 86400.0
        )
        rows.append(
            _exception(
                "settlement_delay",
                row["transaction_id"],
                "medium",
                row["gateway_net"],
                "cash_timing",
                f"Settlement occurred after {age:.2f} days; maximum allowed is {config.max_settlement_days:.2f}.",
            )
        )

    columns = [
        "exception_type",
        "transaction_id",
        "severity",
        "amount_at_risk",
        "exposure_kind",
        "reason",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(rows, columns=columns)
    result = result.sort_values(
        ["severity", "amount_at_risk", "exception_type", "transaction_id"],
        ascending=[True, False, True, True],
        ignore_index=True,
    )
    return result
