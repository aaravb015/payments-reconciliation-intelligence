-- Aggregate each source BEFORE joining. Duplicate records never multiply another feed.
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
