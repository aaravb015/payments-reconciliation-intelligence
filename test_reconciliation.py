import pandas as pd
import pytest

from generate_data import generate_data
from reconcile import reconcile


@pytest.fixture
def feeds():
    internal = pd.DataFrame([dict(transaction_id='001', order_id='O1', merchant_id='M1',
        payment_method='card', currency='INR', internal_status='success',
        created_at='2026-01-01T00:00:00Z', gross_amount=1000.00)])
    gateway = pd.DataFrame([dict(transaction_id='001', gateway_record_id='G1',
        gateway_event_at='2026-01-01T01:00:00Z', gateway_status='success',
        gateway_amount=1000.00, gateway_fee=20.00, gateway_tax=3.60, gateway_net=976.40)])
    bank = pd.DataFrame([dict(transaction_id='001', settlement_line_id='S1',
        settled_at='2026-01-02T01:00:00Z', gross_amount=1000.00,
        fee_amount=20.00, tax_amount=3.60, net_amount=976.40)])
    return internal, gateway, bank


def run(feeds, cutoff='2026-02-10', **kwargs):
    return reconcile(*feeds, as_of=cutoff, **kwargs)



def test_clean_payment(feeds):
    tables = run(feeds)
    assert tables["summary"].iloc[0].exception_count == 0
    assert tables["investigation_queue"].empty
    assert tables["reconciliation_facts"].iloc[0].gateway_bank_net_delta == 0


@pytest.mark.parametrize('cutoff,pending,overdue', [
    ('2026-01-04T00:59:59Z', 1, 0), ('2026-01-04T01:00:00Z', 1, 0), ('2026-01-04T01:00:01Z', 0, 1),
])

def test_pending_due_boundary(feeds, cutoff, pending, overdue):
    i, g, b = feeds
    s = run((i, g, b.iloc[:0]), cutoff)['summary'].iloc[0]
    assert s.pending_payments == pending
    assert s.overdue_payments == overdue
    assert s.overdue_cash_inr == pytest.approx(overdue * 976.4)
    assert s.exception_count == overdue



def test_future_cash_not_used_and_late_cash_stops_outstanding(feeds):
    i, g, b = feeds
    b.loc[0, 'settled_at'] = '2026-01-08T01:00:00Z'
    before = run((i,g,b), '2026-01-05T01:00:00Z')
    assert before['summary'].iloc[0].overdue_cash_inr == 976.4
    assert before['investigation_queue'].iloc[0].age_days == 1
    after = run((i,g,b), '2026-01-08T01:00:00Z')
    assert after['summary'].iloc[0].current_exposure_inr == 0
    assert after['summary'].iloc[0].historical_late_cash_inr == 976.4
    assert after['investigation_queue'].iloc[0].case_status == 'settled_late_review'
    assert after['investigation_queue'].iloc[0].age_days == 4
    assert run((i,g,b), '2026-03-01')['investigation_queue'].iloc[0].age_days == 4

@pytest.mark.parametrize('delta,flagged', [(0.05, False), (0.06, True)])

def test_exact_money_tolerance(feeds, delta, flagged):
    i,g,b = feeds
    g.loc[0,'gateway_amount'] += delta
    types = set(run((i,g,b))['detected_exceptions'].exception_type)
    assert ('amount_mismatch' in types) == flagged
    assert ('settlement_gross_mismatch' in types) == flagged


def test_fee_and_net_overlap_not_double_counted(feeds):
    i,g,b = feeds
    b.loc[0,'fee_amount'] = 25
    b.loc[0,'net_amount'] = 971.4
    t = run((i,g,b))
    assert set(t['detected_exceptions'].exception_type) == {'fee_mismatch','settlement_net_mismatch'}
    s = t['summary'].iloc[0]
    assert s.exception_count == 2
    assert s.exception_transactions == 1
    assert s.current_exposure_inr == 5
    assert len(t['investigation_queue']) == 1


def test_direct_bank_net_and_gross_defects(feeds):
    i,g,b = feeds
    b.loc[0,'net_amount'] = 900
    b.loc[0,'gross_amount'] = 990
    t = run((i,g,b))
    assert set(t['detected_exceptions'].exception_type) == {'settlement_net_mismatch','settlement_gross_mismatch'}
    assert t['summary'].iloc[0].current_exposure_inr == 76.4
    f=t['reconciliation_facts'].iloc[0]
    assert f.gateway_bank_gross_delta == -10
    assert f.gateway_bank_net_delta == -76.4


def test_duplicate_feeds_do_not_create_join_fanout(feeds):
    i,g,b = feeds
    g = pd.concat([g, g.assign(gateway_record_id='G2'), g.assign(gateway_record_id='G3')], ignore_index=True)
    b = pd.concat([b, b.assign(settlement_line_id='S2')], ignore_index=True)
    t = run((i,g,b))
    s = t['summary'].iloc[0]
    assert s.observed_transactions == 1
    assert s.current_exposure_inr == 2000
    assert set(t['detected_exceptions'].exception_type) == {'duplicate_gateway','duplicate_settlement'}
    assert pd.isna(t['reconciliation_facts'].iloc[0].gateway_bank_net_delta)


def test_missing_gateway_with_cash_is_data_investigation(feeds):
    i,g,b = feeds
    t=run((i,g.iloc[:0],b))
    assert t['summary'].iloc[0].exception_count == 1
    assert t['summary'].iloc[0].current_exposure_inr == 0
    assert t['detected_exceptions'].iloc[0].amount_at_risk == 1000
    assert run((i,g.iloc[:0],b.iloc[:0]))['summary'].iloc[0].current_exposure_inr == 1000


def test_status_disagreement_has_no_direct_cash_exposure(feeds):
    i,g,b=feeds
    g.loc[0,'gateway_status']='failed'
    t=run((i,g,b))
    assert set(t['detected_exceptions'].exception_type) == {'status_mismatch'}
    assert t['summary'].iloc[0].current_exposure_inr == 0


def test_original_synthetic_benchmark():
    internal, gateway, bank, truth = generate_data()
    results = reconcile(internal, gateway, bank, as_of="2026-02-10")
    detected = results["detected_exceptions"]
    keys = ["transaction_id", "exception_type"]
    expected = set(truth[keys].itertuples(index=False, name=None))
    core = detected.loc[~detected.exception_type.isin(
        ["settlement_gross_mismatch", "settlement_net_mismatch"])]
    actual = set(core[keys].itertuples(index=False, name=None))
    assert len(expected) == 300
    assert actual == expected
    assert len(results["investigation_queue"]) == 300
    assert len(detected) == 330



def test_truth_columns_are_ignored(feeds):
    original = run(feeds)
    altered = run(tuple(frame.assign(is_fraud=1, exception_type="fake") for frame in feeds))
    for name in original:
        pd.testing.assert_frame_equal(original[name], altered[name])


def test_empty_feeds(feeds):
    results = run(tuple(frame.iloc[:0] for frame in feeds))
    assert results["summary"].iloc[0].observed_transactions == 0
    assert results["investigation_queue"].empty


def test_all_future_rows_excluded(feeds):
    results = run(feeds, "2025-12-31")
    assert results["summary"].iloc[0].observed_transactions == 0
