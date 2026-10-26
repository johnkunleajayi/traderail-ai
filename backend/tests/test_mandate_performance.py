"""Read-only projections over real-service economic fixtures; no PostgreSQL/network."""
from copy import deepcopy
from functools import lru_cache
from datetime import timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4, UUID
from unittest.mock import Mock
from pathlib import Path
import ast, os, subprocess, sys, json
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.sql.dml import Insert,Update,Delete
from app.main import create_app
from app.core.config import Settings
from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.mandate_eligibility.service import resolution_time
from app.mandate_performance import service, valuation
from app.strategy_lab.numeric import exact_sum,exact_product,difference,ratio
from paper_ledger_double import Ledger
from test_mandate_paper import seeded,Session,runner,USER,MID,D,ORIGIN
from test_paper_production_lifecycle import add_candles
from test_integrated_9c_audit import frozen_dataset


@lru_cache(None)
def canonical_case(kind):
    db,at=seeded('no_trades' if kind=='empty' else 'long_profit');session=Session(db)
    if kind!='empty':
        runner.run(session,USER,MID,evaluated_at=at,clock=lambda:at+timedelta(seconds=1));session.commit()
        if kind=='closed':
            ds=frozen_dataset('long_profit');add_candles(db,ds.candles)
            at=ds.candles[-1].close_time+timedelta(minutes=1)
            runner.run(session,USER,MID,evaluated_at=at,clock=lambda:at+timedelta(seconds=1));session.commit()
    return deepcopy(db.rows),at+timedelta(seconds=2)


class ReadSession(Session):
    def in_transaction(self):return False
    def connection(self,**kwargs):
        self.options=kwargs
        return self.db


def fixture(kind='empty'):
    rows,at=canonical_case(kind);db=Ledger();db.rows=deepcopy(rows)
    return db,at,SimpleNamespace(**db.rows['trading_mandates'][0])


def projection(kind='empty'):
    db,at,mandate=fixture(kind)
    return db,at,mandate,service.project(db,mandate,USER,at)


@pytest.fixture
def api():
    clients=[]
    def make(kind='empty'):
        db,at,mandate=fixture(kind);session=ReadSession(db);user=SimpleNamespace(id=USER,status='active')
        app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
        app.dependency_overrides[get_current_user]=lambda:user
        app.dependency_overrides[get_session]=lambda:session
        app.dependency_overrides[resolution_time]=lambda:at
        client=TestClient(app);clients.append(client)
        return SimpleNamespace(db=db,at=at,mandate=mandate,session=session,user=user,app=app,client=client)
    yield make
    for c in clients:c.close()


def get(a):return a.client.get(f'/api/mandates/{MID}/performance')


def test_empty_complete_inventory_allocation_zero_realized_no_fake_wallet():
    db,at,m,r=projection()
    assert r.financial_state=='AVAILABLE',r.reason_codes
    assert r.allocation==r.performance_allocation==r.current_mandate_value==D('500')
    assert r.realized_pnl==r.gross_realized_pnl==r.unrealized_pnl==r.capital_committed==r.capital_exposed==0
    assert r.available_unallocated==D('500') and r.return_percentage==0
    assert r.trade_count==r.completed_trade_count==0 and r.trades==()
    assert r.status=='NO_TRADES_YET' and r.capital_semantics=='ALLOCATION_CONTEXT_NOT_WALLET'
    assert r.valuation.state=='NOT_REQUIRED'
    assert r.drawdown_state=='UNAVAILABLE' and r.drawdown is None


def test_open_capital_cost_basis_fee_inclusive_commitment_fresh_mark_value_and_return():
    db,at,m,r=projection('open');assert r.financial_state=='AVAILABLE',r.reason_codes
    p=db.rows['paper_positions'][0];t=r.trades[0]
    assert r.status=='POSITION_OPEN' and r.trade_count==1 and r.completed_trade_count==0
    assert r.realized_pnl==0
    assert r.capital_exposed==p['entry_cost_basis']
    assert r.capital_committed==exact_sum((p['entry_cost_basis'],p['entry_fee']))
    assert r.available_unallocated==difference(D('500'),r.capital_committed)
    assert r.valuation.state=='FRESH' and r.valuation.price==D('177')
    marked=exact_product(p['quantity'],D('177'))
    assert r.marked_exposure==marked
    assert r.price_unrealized_pnl==difference(marked,p['entry_cost_basis'])
    assert r.unrealized_pnl==difference(r.price_unrealized_pnl,p['entry_fee'])
    assert r.current_mandate_value==exact_sum((D('500'),r.unrealized_pnl))
    assert r.return_percentage==exact_product(ratio(r.unrealized_pnl,D('500')),D('100'))
    assert r.current_mandate_value!=db.rows['paper_portfolios'][0]['cash_balance']
    assert t.entry.quantity==p['quantity'] and t.entry.reference_price==D('177')
    assert t.entry.execution_price==p['average_entry_price'] and t.entry.fee==p['entry_fee']
    assert t.entry.strategy_reason_codes and t.entry.risk_outcome=='APPROVED'
    assert r.valuation.evidence_identity and r.valuation.priced_at<at<r.valuation.valid_until


def test_closed_uses_canonical_pnl_receipts_without_recalculating_or_double_fees():
    db,at,m,r=projection('closed');assert r.financial_state=='AVAILABLE',r.reason_codes
    receipt=db.rows['paper_accounting_entries'][-1];t=r.trades[0]
    assert r.realized_pnl==receipt['net_realized_pnl']
    assert r.gross_realized_pnl==receipt['gross_realized_pnl']
    assert r.current_mandate_value==exact_sum((D('500'),receipt['net_realized_pnl']))
    assert r.capital_committed==r.capital_exposed==r.unrealized_pnl==r.open_entry_fees==0
    assert r.available_unallocated==D('500') and r.completed_trade_count==r.trade_count==1
    assert r.status=='CLOSED' and t.status=='CLOSED' and t.quantity==0
    assert t.exit.reference_price==D('193') and t.exit.fee==receipt['fee']
    assert t.net_realized_pnl==r.realized_pnl and t.gross_realized_pnl==r.gross_realized_pnl
    assert t.entry.authorization_identity!=t.exit.authorization_identity
    assert t.entry.intent_identity!=t.exit.intent_identity
    assert t.entry.accounting_identity!=t.exit.accounting_identity
    assert t.exit.strategy_reason_codes is None  # Not stored by canonical close intent.
    assert r.valuation.state=='NOT_REQUIRED'


@pytest.mark.parametrize('problem',['stale','missing','invalid','future_retrieval','USD_asset','inactive_instrument'])
def test_unavailable_mark_never_fabricates_unrealized_or_current_value(problem):
    db,at,m=fixture('open')
    if problem=='stale':at+=timedelta(hours=2)
    elif problem=='missing':db.rows['market_candles']=[]
    elif problem=='invalid':db.rows['market_candles'][-1]['close']=D('NaN')
    elif problem=='future_retrieval':db.rows['market_candles'][-1]['retrieved_at']=at+timedelta(seconds=1)
    elif problem=='USD_asset':db.rows['assets'][-1]['symbol']='USD'
    else:db.rows['instruments'][0]['status']='inactive'
    r=service.project(db,m,USER,at)
    assert r.financial_state=='AVAILABLE' and r.capital_committed is not None
    assert r.realized_pnl==0
    assert r.valuation.state==('STALE' if problem=='stale' else 'UNAVAILABLE')
    assert r.valuation.price is None
    assert r.unrealized_pnl is r.current_mandate_value is r.return_percentage is None


@pytest.mark.parametrize('kind',['open','closed'])
def test_other_mandate_excluded_even_same_owner_portfolio(kind):
    db,at,m=fixture(kind);m.id=UUID(int=999)
    r=service.project(db,m,USER,at)
    assert r.financial_state=='AVAILABLE' and r.trade_count==0
    assert r.realized_pnl==r.capital_committed==0 and r.trades==()
    assert r.current_mandate_value==D('500')


def test_unrelated_portfolio_cash_changes_do_not_affect_mandate_performance():
    db,at,m,r=projection('closed')
    db.rows['paper_portfolios'][0]['cash_balance']=D('999999')
    again=service.project(db,m,USER,at)
    assert again==r


@pytest.mark.parametrize('problem',['missing_receipt','missing_registry','unknown_order','fill_fee','receipt_pnl','position_owner','position_origin','closing_reference'])
def test_corrupt_or_incomplete_economic_history_never_reports_zero_as_safe(problem):
    db,at,m=fixture('closed')
    if problem=='missing_receipt':db.rows['paper_accounting_entries'].pop()
    elif problem=='missing_registry':db.rows['paper_authorizations'].pop(0)
    elif problem=='unknown_order':db.rows['execution_orders'][-1]['state']='UNKNOWN'
    elif problem=='fill_fee':db.rows['execution_fills'][-1]['fee']+=D('1')
    elif problem=='receipt_pnl':db.rows['paper_accounting_entries'][-1]['net_realized_pnl']+=D('1')
    elif problem=='position_owner':db.rows['paper_positions'][0]['user_id']=uuid4()
    elif problem=='position_origin':db.rows['paper_positions'][0]['origin_decision_identity']='sha256:'+'f'*64
    else:db.rows['paper_positions'][0]['closing_request_identity']='sha256:'+'f'*64
    r=service.project(db,m,USER,at)
    assert r.financial_state==r.status=='UNAVAILABLE'
    assert r.realized_pnl is r.capital_committed is r.current_mandate_value is r.trade_count is None
    assert r.reason_codes==('ATTRIBUTION_RECONCILIATION_REQUIRED',)


def test_allocation_edit_does_not_rewrite_historical_performance_denominator():
    db,at,m=fixture('closed');m.capital_allocation=D('700')
    r=service.project(db,m,USER,at)
    assert r.allocation==D('700') and r.performance_allocation==D('500') and r.allocation_changed
    assert r.current_mandate_value==exact_sum((D('500'),r.realized_pnl))
    assert r.return_percentage==exact_product(ratio(r.realized_pnl,D('500')),D('100'))
    assert r.available_unallocated==D('700')


def test_allocation_reduced_below_commitment_is_explicit_not_negative_cash():
    db,at,m=fixture('open');m.capital_allocation=D('100')
    r=service.project(db,m,USER,at)
    assert r.available_unallocated==0 and r.allocation_shortfall==difference(r.capital_committed,D('100'))
    assert db.rows['paper_portfolios'][0]['cash_balance']>D('9000')


@pytest.mark.parametrize('status',['DRAFT','PAUSED','RETIRED'])
def test_mandate_status_is_not_mutated_by_reporting(status):
    db,at,m=fixture('open');m.status=status
    r=service.project(db,m,USER,at)
    assert r.status==status and r.trades[0].status=='OPEN' and m.status==status


def test_authenticated_api_decimal_strings_read_only_snapshot_and_no_store(api):
    a=api('closed');before=deepcopy(a.db.rows)
    response=get(a);assert response.status_code==200,response.text
    r=response.json();assert r['financial_state']=='AVAILABLE',r
    assert isinstance(r['realized_pnl'],str) and isinstance(r['allocation'],str)
    assert response.headers['cache-control']=='no-store'
    assert a.session.options=={'execution_options':{'isolation_level':'REPEATABLE READ','postgresql_readonly':True}}
    assert a.db.rows==before and a.db.commits==a.db.rollbacks==0
    assert not any(isinstance(s,(Insert,Update,Delete)) for s in a.db.statements)


def test_authentication_and_ownership(api):
    with TestClient(create_app(Settings(_env_file=None))) as c:
        r=c.get(f'/api/mandates/{MID}/performance')
        assert r.status_code==401 and r.headers['cache-control']=='no-store'
    a=api('closed');a.user.id=uuid4();r=get(a)
    assert r.status_code==404 and r.headers['cache-control']=='no-store'


def test_read_cannot_trigger_trading_or_accept_financial_override(api,monkeypatch):
    a=api('open');before=deepcopy(a.db.rows)
    from app.risk import authorization_service
    from app.execution.paper import service as execution
    from app.strategies import contract
    def forbidden(*args,**kw):raise AssertionError('Reporting cannot trade')
    monkeypatch.setattr(authorization_service,'evaluate_and_register',forbidden)
    monkeypatch.setattr(execution,'execute_paper',forbidden)
    monkeypatch.setattr(contract,'evaluate_strategy',forbidden)
    r=a.client.get(f'/api/mandates/{MID}/performance?price=999999&quantity=99&balance=1')
    assert r.status_code==200 and r.json()['valuation']['price']=='177'
    assert a.db.rows==before
    assert a.client.post(f'/api/mandates/{MID}/performance',json={'pnl':'9'}).status_code==405


def test_failed_database_read_is_not_reported_as_empty_inventory(api,monkeypatch):
    a=api('closed')
    def failed(*a,**kw):raise RuntimeError('private details must not be reflected')
    monkeypatch.setattr(a.db,'execute',failed)
    r=get(a);assert r.status_code==503
    assert 'private' not in r.text and r.headers['cache-control']=='no-store'


def test_output_deeply_immutable():
    _,_,_,r=projection('closed')
    with pytest.raises(ValidationError):r.realized_pnl=D('99')
    with pytest.raises(ValidationError):r.trades[0].entry.fee=D('0')


def test_decimal_utc_and_input_order_equivalence():
    db,at,m,r=projection('closed');m.capital_allocation=D('500.000')
    for table in db.rows:db.rows[table].reverse()
    shifted=at.astimezone(timezone(timedelta(hours=1)))
    other=service.project(db,m,USER,shifted)
    assert other.projection_identity==r.projection_identity


def test_four_fresh_hash_seeds():
    script="""
import sys,json
sys.path.insert(0,'tests')
from test_mandate_performance import projection
print(json.dumps([projection(k)[-1].model_dump(mode='json') for k in ('empty','open','closed')],sort_keys=True))
"""
    outputs=[]
    for seed in ('1','7','42','12345'):
        p=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).parents[1],
            env={**os.environ,'PYTHONHASHSEED':seed},capture_output=True,text=True,timeout=120)
        assert p.returncode==0,p.stderr
        outputs.append(p.stdout)
    assert len(set(outputs))==1


def test_direct_projection_cannot_disclose_foreign_mandate():
    from app.mandates.domain import MandateError
    db,at,m=fixture('closed')
    with pytest.raises(MandateError) as error:service.project(db,m,uuid4(),at)
    assert error.value.status==404


def test_reporting_requires_unused_session_and_fresh_api_dependency(api):
    from app.mandate_performance.contracts import ReportingUnavailable
    a=api()
    from inspect import signature
    from app.mandates.router import mandate_performance
    assert signature(mandate_performance).parameters['db'].default.use_cache is False
    a.session.in_transaction=lambda:True
    with pytest.raises(ReportingUnavailable):service.inspect(a.session,USER,MID,evaluated_at=a.at)


def test_incomplete_candle_is_not_a_valuation_mark():
    db,at,m=fixture('open');next_bar=deepcopy(db.rows['market_candles'][-1])
    next_bar['id']=uuid4()
    for key in ('open_time','close_time','retrieved_at'):next_bar[key]+=timedelta(hours=1)
    next_bar['close']=D('99999');db.rows['market_candles'].append(next_bar)
    r=service.project(db,m,USER,at)
    assert r.valuation.state=='FRESH' and r.valuation.price==D('177')


def test_mark_expires_exactly_at_existing_hourly_policy_boundary():
    db,at,m,r=projection('open')
    before=service.project(db,m,USER,r.valuation.valid_until-timedelta(microseconds=1))
    expired=service.project(db,m,USER,r.valuation.valid_until)
    assert before.valuation.state=='FRESH' and expired.valuation.state=='STALE'
    assert expired.current_mandate_value is None


def test_historical_closed_reporting_remains_valid_after_approval_expiry():
    db,at,m,r=projection('closed');later=service.project(db,m,USER,at+timedelta(days=100))
    assert later.financial_state=='AVAILABLE'
    assert later.realized_pnl==r.realized_pnl and later.current_mandate_value==r.current_mandate_value
    assert later.trades==r.trades


def test_approved_unused_authorization_is_not_a_trade_or_commitment():
    from app.mandate_risk.service import evaluate
    db,at=seeded();session=Session(db)
    approval=evaluate(session,USER,MID,evaluated_at=at);assert approval.status.value=='APPROVED'
    m=SimpleNamespace(**db.rows['trading_mandates'][0])
    before=deepcopy(db.rows);r=service.project(db,m,USER,at)
    assert r.financial_state=='AVAILABLE' and r.trade_count==0 and r.capital_committed==0
    assert r.realized_pnl==0 and db.rows==before


def test_multiple_economic_allocation_baselines_are_not_synthesized(monkeypatch):
    db,at,m,r=projection('closed')
    t=r.trades[0];other=t.model_copy(update={'position_id':uuid4(),'allocation_at_entry':D('700')})
    monkeypatch.setattr(service,'load_trades',lambda *args:(t,other))
    mixed=service.project(db,m,USER,at)
    assert mixed.performance_allocation is None and mixed.current_mandate_value is None and mixed.return_percentage is None
    assert mixed.reason_codes==('ALLOCATION_BASELINE_HISTORY_AMBIGUOUS',)
    assert mixed.realized_pnl==exact_sum((t.net_realized_pnl,t.net_realized_pnl))


def test_financial_projection_identity_preserves_nested_decimal_equivalence():
    db,at,m,r=projection('open')
    val=r.valuation.model_copy(update={'price':D('177.000')})
    raw_a=service.raw_values({'valuation':r.valuation,'trade':r.trades[0]})
    raw_b=service.raw_values({'valuation':val,'trade':r.trades[0]})
    from app.mandate_eligibility.contracts import canonical_identity
    assert canonical_identity('performance',raw_a)==canonical_identity('performance',raw_b)


@pytest.mark.parametrize('allocation',[D('0'),D('-1'),D('NaN'),0.5])
def test_invalid_stored_allocation_never_produces_return_or_fake_value(api,allocation):
    a=api();a.db.rows['trading_mandates'][0]['capital_allocation']=allocation
    r=get(a);assert r.status_code==503
    assert 'return_percentage' not in r.json()


def test_future_economic_fact_cannot_be_reported_as_past_performance():
    db,at,m=fixture('closed')
    at=db.rows['paper_positions'][0]['closed_at']-timedelta(microseconds=1)
    r=service.project(db,m,USER,at)
    assert r.financial_state=='UNAVAILABLE' and r.current_mandate_value is None


def test_valid_receipt_hash_cannot_hide_mismatched_risk_book_provenance():
    from dataclasses import replace
    from app.execution.paper.contracts import restore_result
    from app.risk.canonical import to_wire
    from app.strategy_lab.canonical import canonical_json
    db,at,m=fixture('closed');row=db.rows['paper_accounting_entries'][-1]
    changed=replace(restore_result(row['evidence']),before_state_identity='sha256:'+'f'*64)
    row['before_state_identity']=changed.before_state_identity
    row['result_identity']=changed.result_identity;row['evidence']=to_wire(changed)
    row['canonical_payload']=canonical_json(to_wire(changed))
    r=service.project(db,m,USER,at)
    assert r.financial_state=='UNAVAILABLE' and r.realized_pnl is None
