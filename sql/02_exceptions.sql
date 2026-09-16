-- One row per (transaction, rule). Rules read payment_facts only, never labels.
CREATE TABLE exception_signals AS
SELECT transaction_id, 'duplicate_gateway' AS exception_type, 'critical' AS severity,
       gateway_duplicate_excess AS amount_at_risk, 'potential_loss' AS exposure_kind,
       'Multiple gateway records; verify duplicate capture versus repeated feed delivery.' AS reason
FROM payment_facts WHERE gateway_rows > 1
UNION ALL SELECT transaction_id, 'duplicate_settlement', 'critical', settlement_duplicate_excess, 'potential_loss',
       'Multiple settlement lines; verify duplicate cash movement versus repeated feed delivery.'
FROM payment_facts WHERE settlement_rows > 1
UNION ALL SELECT transaction_id, 'missing_gateway', 'high', internal_gross, 'potential_loss',
       'Internal payment has no gateway record at the reporting cutoff.'
FROM payment_facts WHERE has_internal AND gateway_rows = 0
UNION ALL SELECT transaction_id, 'orphan_gateway', 'high', gateway_amount, 'potential_loss',
       'Gateway record has no matching internal payment.'
FROM payment_facts WHERE NOT has_internal AND gateway_rows = 1
UNION ALL SELECT transaction_id, 'orphan_settlement', 'critical', bank_net, 'potential_loss',
       'Bank settlement has no matching internal payment.'
FROM payment_facts WHERE NOT has_internal AND settlement_rows = 1
UNION ALL SELECT transaction_id, 'amount_mismatch', 'high', abs(ledger_gateway_gross_delta), 'potential_loss',
       'Internal gross and gateway gross differ beyond tolerance.'
FROM payment_facts WHERE abs(ledger_gateway_gross_delta) > amount_tolerance
UNION ALL SELECT transaction_id, 'status_mismatch', 'high', internal_gross, 'data_integrity',
       'Internal and gateway statuses disagree.'
FROM payment_facts WHERE has_internal AND gateway_rows = 1 AND internal_status <> gateway_status
UNION ALL SELECT transaction_id, 'missing_settlement', 'critical', gateway_net, 'potential_loss',
       'Successful matched payment is overdue and has no bank settlement.'
FROM payment_facts WHERE settlement_eligible AND settlement_rows = 0 AND as_of > settlement_due_at
UNION ALL SELECT transaction_id, 'fee_mismatch', 'medium', fee_tax_abs_delta, 'potential_loss',
       'Bank fee/tax differs from gateway fee/tax.'
FROM payment_facts WHERE gateway_rows = 1 AND settlement_rows = 1 AND gateway_status = 'success' AND fee_tax_abs_delta > fee_tolerance
UNION ALL SELECT transaction_id, 'settlement_delay', 'medium', gateway_net, 'cash_timing',
       'Cash arrived after the settlement window; historical delay, not current outstanding cash.'
FROM payment_facts WHERE settlement_delay_days > max_settlement_days
-- Phase 2 additions: compare bank gross AND net, including direct net-only defects.
UNION ALL SELECT transaction_id, 'settlement_gross_mismatch', 'high', abs(gateway_bank_gross_delta), 'potential_loss',
       'Bank gross and gateway gross differ beyond tolerance.'
FROM payment_facts WHERE abs(gateway_bank_gross_delta) > amount_tolerance
UNION ALL SELECT transaction_id, 'settlement_net_mismatch', 'high', abs(gateway_bank_net_delta), 'potential_loss',
       'Bank net and gateway net differ beyond tolerance; may overlap fee/gross signals.'
FROM payment_facts WHERE abs(gateway_bank_net_delta) > amount_tolerance;

CREATE TABLE detected_exceptions AS
SELECT e.*, f.merchant_id, f.payment_method, f.gateway_id, f.activity_date,
    CASE WHEN e.exception_type = 'settlement_delay' THEN 'settled_late_review' ELSE 'open_review' END AS case_status,
    CASE WHEN e.exception_type = 'settlement_delay' THEN greatest(f.settlement_delay_days - f.max_settlement_days, 0)
         WHEN e.exception_type = 'missing_settlement' THEN greatest(f.elapsed_days - f.max_settlement_days, 0)
         ELSE greatest(epoch(f.as_of - f.activity_at) / 86400.0, 0) END AS age_days,
    -- Separate unresolved cash from discrepancy estimates and historical timing.
    CASE WHEN e.exception_type IN ('settlement_delay', 'missing_settlement', 'status_mismatch') THEN 0
         WHEN e.exception_type = 'missing_gateway' AND f.settlement_rows > 0 THEN 0
         ELSE e.amount_at_risk END::DECIMAL(18,2) AS financial_exposure_inr,
    CASE WHEN e.exception_type = 'missing_settlement' THEN e.amount_at_risk ELSE 0 END::DECIMAL(18,2) AS overdue_cash_inr,
    CASE WHEN e.exception_type = 'settlement_delay' THEN e.amount_at_risk ELSE 0 END::DECIMAL(18,2) AS historical_late_cash_inr,
    CASE e.exception_type
      WHEN 'missing_settlement' THEN 'Settlement operations: request bank/gateway settlement trace'
      WHEN 'settlement_delay' THEN 'Settlement operations: review SLA breach and root cause'
      WHEN 'fee_mismatch' THEN 'Finance operations: verify fee schedule and tax deductions'
      WHEN 'orphan_settlement' THEN 'Finance operations: identify owner of unmatched bank credit'
      ELSE 'Payment operations: compare source records and investigate discrepancy'
    END AS recommended_action
FROM exception_signals e JOIN payment_facts f USING (transaction_id);
