CREATE TABLE exception_rollup AS
SELECT transaction_id, count(*) AS exception_count,
       string_agg(exception_type, ', ' ORDER BY exception_type) AS exception_types,
       max(CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS severity_rank,
       max(age_days) AS age_days,
       -- Conservative transaction-level proxy: overlapping rule signals are NOT added.
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

-- A common aggregate supports comparable merchant/day/method/provider denominators.
CREATE TABLE dimension_metrics AS
WITH dimensions AS (
 SELECT 'merchant' AS dimension, merchant_id AS dimension_value, * FROM reconciliation_facts
 UNION ALL SELECT 'day', cast(activity_date AS VARCHAR), * FROM reconciliation_facts
 UNION ALL SELECT 'payment_method', payment_method, * FROM reconciliation_facts
 UNION ALL SELECT 'gateway', gateway_id, * FROM reconciliation_facts
 UNION ALL SELECT 'all', 'all', * FROM reconciliation_facts
), totals AS (
 SELECT dimension, dimension_value, count(*) AS observed_transactions,
       count(*) FILTER (WHERE has_internal) AS internal_payments,
       count(*) FILTER (WHERE NOT has_internal) AS orphan_transactions,
       coalesce(sum(exception_count), 0)::BIGINT AS exception_count,
       count(*) FILTER (WHERE exception_count > 0) AS exception_transactions,
       count(*) FILTER (WHERE has_internal AND exception_count > 0) AS internal_exception_payments,
       count(*) FILTER (WHERE reconciliation_state = 'matched') AS matched_payments,
       count(*) FILTER (WHERE settlement_state = 'pending') AS pending_payments,
       count(*) FILTER (WHERE settlement_state = 'overdue') AS overdue_payments,
       coalesce(sum(internal_gross), 0) AS internal_gross_inr,
       coalesce(sum(gateway_gross_total), 0) AS gateway_gross_inr,
       coalesce(sum(gateway_net_total), 0) AS gateway_net_inr,
       coalesce(sum(bank_gross_total), 0) AS bank_gross_inr,
       coalesce(sum(bank_net_total), 0) AS bank_net_inr,
       count(ledger_gateway_gross_delta) AS ledger_gateway_comparable_payments,
       count(gateway_bank_net_delta) AS gateway_bank_comparable_payments,
       coalesce(sum(ledger_gateway_gross_delta), 0) AS ledger_gateway_gross_delta_inr,
       coalesce(sum(gateway_bank_gross_delta), 0) AS gateway_bank_gross_delta_inr,
       coalesce(sum(gateway_bank_net_delta), 0) AS gateway_bank_net_delta_inr,
       coalesce(sum(abs(ledger_gateway_gross_delta)), 0) AS ledger_gateway_gross_abs_delta_inr,
       coalesce(sum(abs(gateway_bank_gross_delta)), 0) AS gateway_bank_gross_abs_delta_inr,
       coalesce(sum(abs(gateway_bank_net_delta)), 0) AS gateway_bank_net_abs_delta_inr,
       coalesce(sum(financial_exposure_inr), 0) AS financial_exposure_inr,
       coalesce(sum(overdue_cash_inr), 0) AS overdue_cash_inr,
       coalesce(sum(financial_exposure_inr + overdue_cash_inr), 0) AS current_exposure_inr,
       coalesce(sum(historical_late_cash_inr), 0) AS historical_late_cash_inr
 FROM dimensions GROUP BY dimension, dimension_value
)
SELECT *, exception_transactions * 1.0 / nullif(observed_transactions, 0) AS exception_transaction_rate,
       internal_exception_payments * 1.0 / nullif(internal_payments, 0) AS internal_exception_rate,
       current_exposure_inr / nullif(sum(current_exposure_inr) OVER (PARTITION BY dimension), 0) AS exposure_share,
       exception_transactions * 1.0 / nullif(sum(exception_transactions) OVER (PARTITION BY dimension), 0) AS exception_share
FROM totals;

CREATE VIEW merchant_metrics AS SELECT * EXCLUDE (dimension) FROM dimension_metrics WHERE dimension = 'merchant';
CREATE VIEW daily_metrics AS SELECT * EXCLUDE (dimension) FROM dimension_metrics WHERE dimension = 'day';
CREATE VIEW payment_method_metrics AS SELECT * EXCLUDE (dimension) FROM dimension_metrics WHERE dimension = 'payment_method';
CREATE VIEW gateway_metrics AS SELECT * EXCLUDE (dimension) FROM dimension_metrics WHERE dimension = 'gateway';
-- Empty inputs still return a single summary row with zero counts and undefined rates.
CREATE VIEW summary AS
SELECT coalesce(d.observed_transactions, 0) AS observed_transactions,
       coalesce(d.internal_payments, 0) AS internal_payments,
       coalesce(d.orphan_transactions, 0) AS orphan_transactions,
       coalesce(d.exception_count, 0) AS exception_count,
       coalesce(d.exception_transactions, 0) AS exception_transactions,
       d.exception_transaction_rate, d.internal_exception_rate,
       coalesce(d.matched_payments, 0) AS matched_payments,
       coalesce(d.pending_payments, 0) AS pending_payments,
       coalesce(d.overdue_payments, 0) AS overdue_payments,
       coalesce(d.internal_gross_inr, 0) AS internal_gross_inr,
       coalesce(d.gateway_gross_inr, 0) AS gateway_gross_inr,
       coalesce(d.gateway_net_inr, 0) AS gateway_net_inr,
       coalesce(d.bank_gross_inr, 0) AS bank_gross_inr,
       coalesce(d.bank_net_inr, 0) AS bank_net_inr,
       coalesce(d.financial_exposure_inr, 0) AS financial_exposure_inr,
       coalesce(d.overdue_cash_inr, 0) AS overdue_cash_inr,
       coalesce(d.current_exposure_inr, 0) AS current_exposure_inr,
       coalesce(d.historical_late_cash_inr, 0) AS historical_late_cash_inr
FROM (SELECT 1) seed LEFT JOIN dimension_metrics d ON d.dimension = 'all';

CREATE VIEW exception_type_metrics AS
SELECT exception_type, severity, exposure_kind, count(*) AS exception_count,
       count(*) * 1.0 / nullif((SELECT count(*) FROM payment_facts), 0) AS observed_transaction_rate,
       sum(amount_at_risk) AS rule_amount_at_risk_inr,
       sum(financial_exposure_inr) AS rule_financial_exposure_inr,
       sum(overdue_cash_inr) AS overdue_cash_inr,
       sum(historical_late_cash_inr) AS historical_late_cash_inr
FROM detected_exceptions GROUP BY exception_type, severity, exposure_kind;

CREATE VIEW aging_metrics AS
SELECT case_status, aging_bucket, count(*) AS investigation_count,
       sum(financial_exposure_inr) AS financial_exposure_inr,
       sum(overdue_cash_inr) AS overdue_cash_inr,
       sum(current_exposure_inr) AS current_exposure_inr,
       sum(historical_late_cash_inr) AS historical_late_cash_inr
FROM investigation_queue GROUP BY case_status, aging_bucket;

CREATE VIEW settlement_delay_metrics AS
SELECT gateway_id, count(settlement_delay_days) AS observed_unique_settlements,
       count(*) FILTER (WHERE settlement_delay_days > max_settlement_days) AS late_settlements,
       avg(settlement_delay_days) AS mean_delay_days,
       median(settlement_delay_days) AS median_delay_days,
       quantile_cont(settlement_delay_days, 0.95) AS p95_delay_days,
       max(settlement_delay_days) AS max_delay_days,
       count(*) FILTER (WHERE settlement_state = 'overdue') AS overdue_payments,
       count(*) FILTER (WHERE settlement_state = 'pending') AS pending_payments
FROM reconciliation_facts GROUP BY gateway_id;

-- Actual bank cash date, separate from the transaction activity-date cohort.
CREATE VIEW daily_settlements AS
SELECT settled_at::DATE AS settlement_date, count(*) AS settlement_lines,
       count(DISTINCT transaction_id) AS settled_transactions,
       sum(gross_amount) AS bank_gross_inr, sum(fee_amount) AS bank_fee_inr,
       sum(tax_amount) AS bank_tax_inr, sum(net_amount) AS bank_net_inr
FROM settlement GROUP BY settled_at::DATE;
