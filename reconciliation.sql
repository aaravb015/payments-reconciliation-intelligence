CREATE VIEW gateway_rollup AS
SELECT transaction_id, count(*) AS gateway_rows,
       CASE WHEN count(DISTINCT gateway_id) = 1 THEN min(gateway_id) ELSE 'multiple' END AS gateway_id,
       min(gateway_event_at) AS gateway_event_at, min(gateway_status) AS gateway_status,
       sum(gateway_amount) AS gateway_gross_total, sum(gateway_net) AS gateway_net_total,
       max(gateway_amount) AS gateway_amount, max(gateway_net) AS gateway_net,
       max(gateway_fee) AS gateway_fee, max(gateway_tax) AS gateway_tax,
       sum(gateway_amount) - max(gateway_amount) AS gateway_duplicate_excess
FROM gateway GROUP BY transaction_id;

CREATE VIEW settlement_rollup AS
SELECT transaction_id, count(*) AS settlement_rows, min(settled_at) AS settled_at,
       sum(gross_amount) AS bank_gross_total, sum(net_amount) AS bank_net_total,
       max(gross_amount) AS bank_gross, max(net_amount) AS bank_net,
       max(fee_amount) AS bank_fee, max(tax_amount) AS bank_tax,
       sum(net_amount) - max(net_amount) AS settlement_duplicate_excess
FROM settlement GROUP BY transaction_id;

CREATE TABLE payment_facts AS
WITH ids AS (
    SELECT transaction_id FROM internal UNION SELECT transaction_id FROM gateway UNION SELECT transaction_id FROM settlement
), joined AS (
    SELECT ids.transaction_id, i.transaction_id IS NOT NULL AS has_internal,
       coalesce(i.merchant_id, 'unknown') AS merchant_id,
       coalesce(i.payment_method, 'unknown') AS payment_method,
       coalesce(g.gateway_id, 'unknown') AS gateway_id,
       coalesce(i.created_at, g.gateway_event_at, s.settled_at) AS activity_at,
       i.created_at, i.internal_status, i.gross_amount AS internal_gross,
       coalesce(g.gateway_rows, 0) AS gateway_rows, coalesce(s.settlement_rows, 0) AS settlement_rows,
       g.gateway_event_at, g.gateway_status, s.settled_at,
       g.gateway_gross_total, g.gateway_net_total, s.bank_gross_total, s.bank_net_total,
       g.gateway_amount, g.gateway_net, g.gateway_fee, g.gateway_tax,
       s.bank_gross, s.bank_net, s.bank_fee, s.bank_tax,
       g.gateway_duplicate_excess, s.settlement_duplicate_excess,
       c.as_of, c.max_settlement_days, c.amount_tolerance, c.fee_tolerance
    FROM ids LEFT JOIN internal i USING (transaction_id)
    LEFT JOIN gateway_rollup g USING (transaction_id)
    LEFT JOIN settlement_rollup s USING (transaction_id) CROSS JOIN run_config c
)
SELECT *, activity_at::DATE AS activity_date,
    gateway_event_at + max_settlement_days * INTERVAL '1 day' AS settlement_due_at,
    epoch(as_of - gateway_event_at) / 86400.0 AS elapsed_days,
    CASE WHEN gateway_rows = 1 AND settlement_rows = 1 AND gateway_status = 'success'
         THEN epoch(settled_at - gateway_event_at) / 86400.0 END AS settlement_delay_days,
    has_internal AND gateway_rows = 1 AND internal_status = 'success' AND gateway_status = 'success' AS settlement_eligible,
    CASE WHEN has_internal AND gateway_rows = 1 THEN gateway_amount - internal_gross END AS ledger_gateway_gross_delta,
    CASE WHEN gateway_rows = 1 AND settlement_rows = 1 THEN bank_gross - gateway_amount END AS gateway_bank_gross_delta,
    CASE WHEN gateway_rows = 1 AND settlement_rows = 1 THEN bank_net - gateway_net END AS gateway_bank_net_delta,
    CASE WHEN gateway_rows = 1 AND settlement_rows = 1
         THEN abs(gateway_fee - bank_fee) + abs(gateway_tax - bank_tax) END AS fee_tax_abs_delta
FROM joined;

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

CREATE TABLE exception_rollup AS
SELECT transaction_id, count(*) AS exception_count,
       string_agg(exception_type, ', ' ORDER BY exception_type) AS exception_types,
       max(CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS severity_rank,
       max(age_days) AS age_days,
       max(financial_exposure_inr) AS financial_exposure_inr,
       max(overdue_cash_inr) AS overdue_cash_inr,
       max(historical_late_cash_inr) AS historical_late_cash_inr,
       string_agg(DISTINCT recommended_action, '; ' ORDER BY recommended_action) AS recommended_actions,
       bool_and(case_status = 'settled_late_review') AS historical_only
FROM detected_exceptions GROUP BY transaction_id;

CREATE TABLE reconciliation_facts AS
SELECT f.*, coalesce(e.exception_count, 0) AS exception_count,
       coalesce(e.exception_types, '') AS exception_types,
       coalesce(e.financial_exposure_inr, 0) AS financial_exposure_inr,
       coalesce(e.overdue_cash_inr, 0) AS overdue_cash_inr,
       coalesce(e.historical_late_cash_inr, 0) AS historical_late_cash_inr,
       CASE WHEN f.gateway_rows > 1 OR f.settlement_rows > 1 THEN 'ambiguous_duplicate'
            WHEN f.settlement_eligible AND f.settlement_rows = 0 AND f.as_of <= f.settlement_due_at THEN 'pending'
            WHEN f.settlement_eligible AND f.settlement_rows = 0 THEN 'overdue'
            WHEN f.settlement_eligible AND f.settlement_rows = 1 THEN 'settled'
            ELSE 'not_eligible_or_unmatched' END AS settlement_state,
       CASE WHEN e.exception_count > 0 THEN 'exception'
            WHEN f.settlement_eligible AND f.settlement_rows = 0 THEN 'pending'
            WHEN f.has_internal AND f.gateway_rows = 1 AND f.settlement_rows = 1 THEN 'matched'
            ELSE 'no_exception_not_three_way' END AS reconciliation_state
FROM payment_facts f LEFT JOIN exception_rollup e USING (transaction_id);

CREATE VIEW investigation_queue AS
WITH ranked AS (
 SELECT f.transaction_id, f.merchant_id, f.payment_method, f.gateway_id,
        e.exception_types, e.exception_count,
        CASE e.severity_rank WHEN 4 THEN 'critical' WHEN 3 THEN 'high' WHEN 2 THEN 'medium' ELSE 'low' END AS severity,
        CASE WHEN e.historical_only THEN 'settled_late_review' ELSE 'open_review' END AS case_status,
        e.financial_exposure_inr, e.overdue_cash_inr, e.historical_late_cash_inr,
        e.financial_exposure_inr + e.overdue_cash_inr AS current_exposure_inr,
        e.age_days, e.recommended_actions,
        e.severity_rank * 100 + least((e.financial_exposure_inr + e.overdue_cash_inr) / 10000.0, 30)
          + least(e.age_days, 30) AS priority_score
 FROM reconciliation_facts f JOIN exception_rollup e USING (transaction_id)
)
SELECT row_number() OVER (ORDER BY priority_score DESC, current_exposure_inr DESC, age_days DESC, transaction_id) AS priority_rank,
       *, CASE WHEN age_days <= 0 THEN '0 days' WHEN age_days <= 3 THEN '0-3 days'
               WHEN age_days <= 7 THEN '3-7 days' WHEN age_days <= 14 THEN '7-14 days'
               WHEN age_days <= 30 THEN '14-30 days' ELSE '30+ days' END AS aging_bucket
FROM ranked;
