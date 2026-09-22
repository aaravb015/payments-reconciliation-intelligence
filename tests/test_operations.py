"""Hand-calculated operational controls, plus V1 compatibility and leakage guards."""
import ast
import inspect
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.data_generator import GenerationConfig, generate_data
from src.operations import run_operations
from src.reconciliation import ReconciliationConfig, reconcile


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
    return run_operations(*feeds, as_of=cutoff, **kwargs).tables


def test_clean_financial_controls(feeds):
    tables = run(feeds)
    s = tables['summary'].iloc[0]
    assert s.matched_payments == 1
    assert s.exception_count == s.current_exposure_inr == 0
    assert s.internal_gross_inr == s.gateway_gross_inr == s.bank_gross_inr == 1000
    assert s.bank_net_inr == s.gateway_net_inr == 976.4
    assert s.exception_transaction_rate == 0
    assert tables['settlement_delay_metrics'].iloc[0].mean_delay_days == 1
    f = tables['reconciliation_facts'].iloc[0]
    assert f.gateway_bank_gross_delta == f.gateway_bank_net_delta == 0


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
    assert before['summary'].iloc[0].bank_net_inr == 0
    assert before['investigation_queue'].iloc[0].age_days == 1
    after = run((i,g,b), '2026-01-08T01:00:00Z')
    assert after['summary'].iloc[0].current_exposure_inr == 0
    assert after['summary'].iloc[0].historical_late_cash_inr == 976.4
    assert after['investigation_queue'].iloc[0].case_status == 'settled_late_review'
    assert after['investigation_queue'].iloc[0].age_days == 4
    assert run((i,g,b), '2026-03-01')['investigation_queue'].iloc[0].age_days == 4


def test_future_all_feeds_excluded_and_timezone(feeds):
    result = run_operations(*feeds, as_of='2025-12-31T23:00:00Z')
    assert result.tables['summary'].iloc[0].observed_transactions == 0
    assert all(x['future_rows_excluded'] == 1 for x in result.metadata['source_counts'].values())
    utc = run(feeds, '2026-01-02T01:00:00Z')
    offset = run(feeds, '2026-01-02T06:30:00+05:30')
    assert_frame_equal(utc['summary'], offset['summary'])


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
    assert s.exception_transaction_rate == 1  # transaction rate never doubles
    assert s.current_exposure_inr == 5
    assert t['exception_type_metrics'].rule_financial_exposure_inr.sum() == 10
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
    assert s.gateway_gross_inr == 3000
    assert s.bank_net_inr == 1952.8
    assert s.current_exposure_inr == 2000  # sum minus retained largest gateway row
    assert set(t['detected_exceptions'].exception_type) == {'duplicate_gateway','duplicate_settlement'}
    assert pd.isna(t['reconciliation_facts'].iloc[0].gateway_bank_net_delta)
    assert t['merchant_metrics'].iloc[0].gateway_bank_comparable_payments == 0


def test_unequal_duplicate_exposure(feeds):
    i,g,b = feeds
    b = pd.concat([b, b.assign(settlement_line_id='S2', net_amount=100), b.assign(settlement_line_id='S3', net_amount=200)], ignore_index=True)
    t = run((i,g,b))
    assert t['summary'].iloc[0].current_exposure_inr == 300


def test_orphans_unknown_dimensions_and_denominators(feeds):
    i,g,b = feeds
    g = pd.concat([g, g.assign(transaction_id='ORPHAN', gateway_record_id='G2')], ignore_index=True)
    t = run((i,g,b))
    s = t['summary'].iloc[0]
    assert s.observed_transactions == 2
    assert s.internal_payments == 1
    assert s.orphan_transactions == 1
    assert s.exception_transaction_rate == 0.5
    assert s.internal_exception_rate == 0
    unknown = t['merchant_metrics'].set_index('dimension_value').loc['unknown']
    assert unknown.current_exposure_inr == 1000
    assert pd.isna(unknown.internal_exception_rate)
    assert set(t['gateway_metrics'].dimension_value) == {'unknown'}


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


@pytest.mark.parametrize('days,bucket', [(0,'0 days'), (3,'0-3 days'), (3.5,'3-7 days'), (7,'3-7 days'), (14,'7-14 days'), (30,'14-30 days'), (31,'30+ days')])
def test_aging_boundaries(feeds, days, bucket):
    i,g,b=feeds
    cutoff=pd.Timestamp('2026-01-01')+pd.Timedelta(days=days)
    t=run((i,g.iloc[:0],b.iloc[:0]),cutoff)
    assert t['investigation_queue'].iloc[0].aging_bucket == bucket


def test_priority_combines_severity_amount_age_and_stable_ties(feeds):
    i,g,b=feeds
    i=pd.concat([i.assign(transaction_id=tx, gross_amount=amount, created_at=date) for tx,amount,date in [
        ('low','100','2026-01-01'), ('high','10000','2026-01-01'), ('older','10000','2025-12-31'), ('tie','10000','2026-01-01')]],ignore_index=True)
    q=run((i,g.iloc[:0],b.iloc[:0]),'2026-01-05')['investigation_queue']
    assert list(q.transaction_id) == ['older','high','tie','low']
    assert list(q.priority_rank) == [1,2,3,4]
    assert q.iloc[1].priority_score == 305  # high 300 + INR 10k / 10k + 4 days


def test_provider_attribution_and_ambiguous_provider(feeds):
    i,g,b=feeds
    g['gateway_id']='processor_a'
    assert list(run((i,g,b))['gateway_metrics'].dimension_value) == ['processor_a']
    g=pd.concat([g,g.assign(gateway_record_id='G2',gateway_id='processor_b')],ignore_index=True)
    assert list(run((i,g,b))['gateway_metrics'].dimension_value) == ['multiple']


@pytest.mark.parametrize('seed',[7,42,101])
def test_sql_retains_v1_core_rules_on_matured_snapshot(seed):
    i,g,b,_=generate_data(GenerationConfig(seed=seed,n_transactions=1000))
    reference=reconcile(i,g,b)
    detected=run((i,g,b))['detected_exceptions']
    core=detected[detected.exception_type.isin(reference.exception_type.unique())]
    cols=['exception_type','transaction_id','severity','amount_at_risk','exposure_kind']
    assert_frame_equal(core[cols].sort_values(cols[:2]).reset_index(drop=True),reference[cols].sort_values(cols[:2]).reset_index(drop=True))


def test_all_dimension_totals_tie_out_and_input_order_is_irrelevant():
    i,g,b,_=generate_data(GenerationConfig(n_transactions=500))
    t=run((i,g,b))
    shuffled=run(tuple(f.sample(frac=1,random_state=3) for f in (i,g,b)))
    for name in t:
        assert_frame_equal(t[name],shuffled[name])
    for name in ['merchant_metrics','daily_metrics','payment_method_metrics','gateway_metrics']:
        for col in ['observed_transactions','internal_payments','exception_count','exception_transactions','internal_gross_inr','gateway_net_inr','bank_net_inr','current_exposure_inr','historical_late_cash_inr']:
            assert t[name][col].sum() == pytest.approx(t['summary'].iloc[0][col])
        assert t[name].exposure_share.sum() == pytest.approx(1)
    assert t['daily_settlements'].bank_net_inr.sum() == pytest.approx(t['summary'].iloc[0].bank_net_inr)


def test_empty_sources(feeds):
    t=run(tuple(f.iloc[:0] for f in feeds))
    assert t['summary'].iloc[0].observed_transactions == 0
    assert pd.isna(t['summary'].iloc[0].exception_transaction_rate)
    assert t['investigation_queue'].empty


@pytest.mark.parametrize('fault', ['missing_column','null_key','duplicate_internal','currency','nan_amount','negative_amount','fractional_paise','invalid_time'])
def test_invalid_source_contract_rejected(feeds, fault):
    i,g,b=feeds
    if fault=='missing_column': i=i.drop(columns='merchant_id')
    elif fault=='null_key': g.loc[0,'transaction_id']=None
    elif fault=='duplicate_internal': i=pd.concat([i,i],ignore_index=True)
    elif fault=='currency': i.loc[0,'currency']='USD'
    elif fault=='nan_amount': g.loc[0,'gateway_net']=float('nan')
    elif fault=='negative_amount': g.loc[0,'gateway_net']=-1
    elif fault=='fractional_paise': g.loc[0,'gateway_net']=1.001
    elif fault=='invalid_time': b.loc[0,'settled_at']='not-a-date'
    with pytest.raises(ValueError): run((i,g,b))


@pytest.mark.parametrize('field', ['amount_tolerance','fee_tolerance','max_settlement_days'])
def test_invalid_configuration_rejected(feeds,field):
    with pytest.raises(ValueError): run(feeds,config=ReconciliationConfig(**{field:-1}))


def test_truth_columns_never_enter_detection_and_inputs_not_mutated(feeds):
    originals=tuple(f.copy(deep=True) for f in feeds)
    plain=run(feeds)
    contaminated=tuple(f.assign(truth_exception_id='X', exception_type='fabricated', reference_amount=999999) for f in feeds)
    other=run(contaminated)
    for name in plain:
        assert_frame_equal(plain[name],other[name])
    for original,frame in zip(originals,feeds): assert_frame_equal(original,frame)
    assert 'truth' not in inspect.signature(run_operations).parameters
    # Guard import boundaries: production modules cannot depend on generation/evaluation.
    for path in ['src/operations.py','src/reporting.py','scripts/run_operations.py']:
        tree=ast.parse(Path(path).read_text())
        assert not any(isinstance(n,ast.ImportFrom) and n.module in {'src.data_generator','src.evaluation'} for n in ast.walk(tree))
