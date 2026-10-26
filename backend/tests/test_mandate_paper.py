"""Real mandate/Risk/registry/production execution over the existing SQL ledger double.
No PostgreSQL/network. This is not a physical locking/trigger/concurrency proof.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID,uuid4
from contextlib import contextmanager
import os,sys,json,subprocess
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import create_app
from app.core.config import Settings
from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.mandate_eligibility.service import resolution_time
from app.mandate_paper import service as runner
from app.mandate_paper.contracts import PaperRunError
from app.mandate_risk import service as mandate_risk
from app.execution.paper.book import capture_book
from app.execution.registry.repository import load_authorization
from app.execution.registry.models import authorizations
from app.execution.request import ExecutionRequest
from app.strategy_lab.numeric import exact_sum,difference
from app.risk.vocabulary import KillSwitchScope
from app.risk.paper_source.policies import COSTS
from app.mandates.models import TradingMandate
from app.instruments.models import Instrument
from paper_ledger_double import Ledger
from test_paper_production_lifecycle import ready,add_candles,USER,PORT,ORIGIN
from test_mandate_strategy import rows_for

D=Decimal
MID=UUID(int=77)

class Session:
    def __init__(self,db):self.db=db;self.saved=deepcopy(db.rows)
    def scalar(self,query):
        entity=query.column_descriptions[0]['entity']
        if entity in (TradingMandate,Instrument):
            row=self.db.execute(query).mappings().one_or_none()
            return SimpleNamespace(**row) if row else None
        return self.db.execute(query).scalar_one_or_none()
    def connection(self):return self.db
    def commit(self):self.saved=deepcopy(self.db.rows);self.db.commits+=1
    def rollback(self):self.db.rows=deepcopy(self.saved);self.db.rollbacks+=1


def seeded(kind='long_profit'):
    reference,*_=ready()
    db=Ledger()
    for table in ('users','paper_portfolios','assets','instruments'):
        db.rows[table]=deepcopy(reference.rows[table])
    _,candles,at=rows_for(kind);add_candles(db,candles)
    db.rows['trading_mandates']=[dict(id=MID,user_id=USER,environment='PAPER',capital_allocation=D('500'),
        allocation_currency='USDT',instrument='BTC/USDT',risk_level='BALANCED',trading_style='MEDIUM_TERM',
        direction_capability='LONG_ONLY',status='ACTIVE',created_at=at-timedelta(days=10),updated_at=at-timedelta(days=10))]
    return db,at


@pytest.fixture
def api(monkeypatch):
    def forbidden(*a,**k):raise AssertionError('No provider/network/market ingestion')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',forbidden)
    clients=[]
    def make(kind='long_profit'):
        db,at=seeded(kind);session=Session(db);user=SimpleNamespace(id=USER,status='active')
        now=[at];executed=[at+timedelta(seconds=1)]
        app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
        app.dependency_overrides[get_current_user]=lambda:user
        app.dependency_overrides[get_session]=lambda:session
        app.dependency_overrides[resolution_time]=lambda:now[0]
        app.dependency_overrides[runner.execution_clock]=lambda:lambda:executed[0]
        client=TestClient(app);clients.append(client)
        return SimpleNamespace(db=db,session=session,user=user,now=now,executed=executed,client=client,app=app)
    yield make
    for client in clients:client.close()


def post(a,body=None,origin=ORIGIN):
    return a.client.post(f'/api/mandates/{MID}/paper-run',json={} if body is None else body,
        headers={} if origin is None else {'Origin':origin})


def economic_counts(db):
    return {t:len(db.rows[t]) for t in ('paper_authorized_commands','execution_requests',
        'execution_approval_consumptions','execution_orders','execution_fills','paper_positions','paper_accounting_entries')}


def test_real_end_to_end_entry_exact_risk_quantity_costs_and_provenance(api):
    a=api();response=post(a);assert response.status_code==200,response.text
    r=response.json();assert r['status']=='PAPER_EXECUTED' and r['risk_outcome']=='APPROVED'
    assert r['strategy_decision']==r['strategy_outcome']=='ENTER_LONG'
    assert economic_counts(a.db)=={t:1 for t in economic_counts(a.db)}
    package=load_authorization(a.db,r['registry_identity'],user_id=USER)
    e=r['execution'];q=D(r['approved_quantity'])
    assert q==D(e['quantity'])==package.authorization.quantity
    commitment=exact_sum((D(e['gross_notional']),D(e['fee'])))
    assert commitment<=D('500')
    assert a.db.rows['paper_portfolios'][0]['cash_balance']==difference(D('10000'),commitment)==D(e['cash_after'])
    assert a.db.rows['paper_portfolios'][0]['starting_balance']==D('10000')
    assert package.inputs.snapshot.available_cash==D('10000')
    assert package.inputs.sizing_assumptions.costs==COSTS
    assert package.inputs.sizing_assumptions.proposed_quantity is None
    position=a.db.rows['paper_positions'][0]
    assert position['quantity']==q and position['status']=='OPEN' and position['currency']=='USDT'
    assert position['origin_intent_identity']==r['intent_identity']
    assert position['origin_decision_identity']==r['decision_identity']
    assert position['opening_order_identity']==e['order_identity'] and position['opening_fill_identity']==e['fill_identity']
    provenance=package.inputs.intent.entry_context['mandate_provenance']
    assert provenance['mandate_identity']==str(MID)
    assert provenance['eligibility_identity']==r['eligibility_identity']
    assert provenance['strategy_observation_identity']==r['strategy_observation_identity']
    assert a.db.rows['execution_orders'][0]['state']=='FILLED'
    assert a.db.rows['execution_requests'][0]['venue_identity']=='PAPER_INTERNAL'
    assert a.db.rows['paper_accounting_entries'][0]['net_realized_pnl'] is None
    assert a.db.commits==1 and response.headers['cache-control']=='no-store'


@pytest.mark.parametrize('kind',['no_trades','short_profit'])
def test_no_trade_and_unsupported_short_do_not_execute(api,monkeypatch,kind):
    a=api(kind);blocked=Mock(side_effect=AssertionError('No execution for NO_TRADE'))
    monkeypatch.setattr(runner,'run_paper_execution',blocked)
    r=post(a);assert r.status_code==200,r.text
    assert r.json()['status']=='NO_TRADE' and r.json()['execution'] is None
    if kind=='short_profit':assert r.json()['strategy_decision']=='ENTER_SHORT'
    assert all(n==0 for n in economic_counts(a.db).values())
    assert not a.db.rows['paper_authorizations'];blocked.assert_not_called()


def test_risk_rejection_never_executes(api,monkeypatch):
    a=api();a.db.rows['trading_mandates'][0]['capital_allocation']=D('10000');a.session.saved=deepcopy(a.db.rows)
    blocked=Mock(side_effect=AssertionError('Risk rejection cannot execute'))
    monkeypatch.setattr(runner,'run_paper_execution',blocked)
    r=post(a);assert r.status_code==200,r.text
    assert r.json()['status']=='RISK_REJECTED' and r.json()['risk_outcome']=='REJECTED'
    assert not a.db.rows['paper_authorizations'] and all(n==0 for n in economic_counts(a.db).values())
    blocked.assert_not_called()


def test_successful_retry_recovers_after_expiry_without_strategy_or_risk(api,monkeypatch):
    a=api();first=post(a);assert first.status_code==200,first.text
    a.db.rows['trading_mandates'][0]['status']='PAUSED';a.session.saved=deepcopy(a.db.rows)
    state=deepcopy(a.db.rows);original=first.json()
    a.now[0]+=timedelta(days=2);a.executed[0]=a.now[0]
    def no_new_decision(*a,**k):raise AssertionError('Recover committed fact, not evaluate/resize')
    monkeypatch.setattr(runner,'evaluate_risk',no_new_decision)
    r=post(a);assert r.status_code==200,r.text
    result=r.json();assert result['reused'] is True
    assert result['execution']==original['execution']
    assert result['authorization_identity']==original['authorization_identity']
    assert a.db.rows==state
    assert economic_counts(a.db)=={t:1 for t in economic_counts(a.db)}


def test_completed_receipt_remains_recoverable_after_pause(api,monkeypatch):
    a=api();original=post(a).json();a.db.rows['trading_mandates'][0]['status']='PAUSED'
    a.session.saved=deepcopy(a.db.rows)
    monkeypatch.setattr(runner,'evaluate_risk',Mock(side_effect=AssertionError('No new action while paused')))
    r=post(a);assert r.status_code==200,r.text
    assert r.json()['reused'] is True and r.json()['execution']==original['execution']


def test_expiry_is_rechecked_at_execution_instant_and_rolls_back_everything(api):
    a=api();before=deepcopy(a.db.rows);a.executed[0]=a.now[0]+timedelta(minutes=6)
    r=post(a);assert r.status_code==409,r.text
    assert r.json()['detail']['code']=='paper_authorization_expired'
    assert a.db.rows==before


def test_entire_risk_and_execution_transaction_rolls_back_on_accounting_failure(api):
    a=api();before=deepcopy(a.db.rows);a.db.fail_table='paper_accounting_entries'
    r=post(a);assert r.status_code==503,r.text
    assert a.db.rows==before
    assert all(n==0 for n in economic_counts(a.db).values())
    assert not a.db.rows['paper_authorizations']


@pytest.mark.parametrize('body',[{'quantity':'1'},{'price':'1'},{'fees':'0'},{'balance':'99999'},
    {'strategy':'ENTER_LONG'},{'risk':'APPROVED'},{'authorization_identity':'sha256:'+'f'*64}])
def test_financial_http_truth_cannot_be_injected(api,body):
    a=api();assert post(a,body).status_code==422
    assert not a.db.rows['paper_authorizations'] and all(n==0 for n in economic_counts(a.db).values())


@pytest.mark.parametrize('origin',[None,'https://evil.example',ORIGIN+'/'])
def test_trusted_origin_enforced(api,origin):
    a=api();assert post(a,origin=origin).status_code==403
    assert not a.db.rows['paper_authorizations']


def test_ownership_isolation(api):
    a=api();a.user.id=uuid4();r=post(a);assert r.status_code==404,r.text
    assert all(n==0 for n in economic_counts(a.db).values())


def test_anonymous_401():
    with TestClient(create_app(Settings(_env_file=None))) as c:
        assert c.post(f'/api/mandates/{MID}/paper-run',json={},headers={'Origin':ORIGIN}).status_code==401


@pytest.mark.parametrize('change',['USD','LIVE'])
def test_portfolio_currency_and_environment_cannot_be_substituted(api,change):
    a=api();row=a.db.rows['paper_portfolios'][0]
    row['base_currency' if change=='USD' else 'environment']=change;a.session.saved=deepcopy(a.db.rows)
    r=post(a);assert r.status_code==409,r.text
    assert all(n==0 for n in economic_counts(a.db).values())


def test_execution_never_expands_a_stricter_risk_approved_size(api,monkeypatch):
    a=api();original=mandate_risk.assemble
    def stricter_server_hypothesis(*args,**kwargs):
        inputs,decision=original(*args,**kwargs)
        return replace(inputs,sizing_assumptions=replace(inputs.sizing_assumptions,requested_risk_fraction=D('.00001'))),decision
    monkeypatch.setattr(mandate_risk,'assemble',stricter_server_hypothesis)
    r=post(a);assert r.status_code==200,r.text
    e=r.json()['execution']
    assert exact_sum((D(e['gross_notional']),D(e['fee'])))<D('100')
    assert D(e['quantity'])==D(r.json()['approved_quantity'])


def test_mismatched_risk_authorization_is_not_executed(api,monkeypatch):
    a=api();original=runner.evaluate_risk
    def wrong(*args,**kwargs):
        result=original(*args,**kwargs)
        auth=result.authorization.model_copy(update={'quantity':result.authorization.quantity+D('1')})
        return result.model_copy(update={'authorization':auth})
    monkeypatch.setattr(runner,'evaluate_risk',wrong)
    r=post(a);assert r.status_code==409,r.text
    assert r.json()['detail']['code']=='mandate_authorization_mismatch'
    assert all(n==0 for n in economic_counts(a.db).values())
    assert not a.db.rows['paper_authorizations']


@pytest.mark.parametrize('problem',['command_only','consumption_only','receipt_missing','unknown_order','extra_fill'])
def test_unknown_or_incomplete_facts_never_blindly_resubmit(api,monkeypatch,problem):
    a=api()
    if problem in ('command_only','consumption_only'):
        response=a.client.post(f'/api/mandates/{MID}/risk-evaluate',json={},headers={'Origin':ORIGIN})
        assert response.status_code==200,response.text
        package=load_authorization(a.db,response.json()['authorization']['registry_identity'],user_id=USER)
        request=ExecutionRequest(package.authorization,'PAPER_INTERNAL',package.authorization.decision.evaluated_at)
        if problem=='command_only':
            a.db.rows['paper_authorized_commands'].append(dict(request_identity=request.request_identity,user_id=USER))
        else:
            a.db.rows['execution_approval_consumptions'].append(dict(decision_identity=request.risk_decision_identity,user_id=USER,consumption_identity='sha256:'+'f'*64))
    else:
        assert post(a).status_code==200
        if problem=='receipt_missing':a.db.rows['paper_accounting_entries']=[]
        elif problem=='unknown_order':a.db.rows['execution_orders'][0]['state']='UNKNOWN'
        else:
            other=deepcopy(a.db.rows['execution_fills'][0]);other['fill_identity']='sha256:'+'f'*64
            a.db.rows['execution_fills'].append(other)
    a.session.saved=deepcopy(a.db.rows);before=deepcopy(a.db.rows)
    blocked=Mock(side_effect=AssertionError('Unknown must not evaluate/re-submit'))
    monkeypatch.setattr(runner,'run_paper_execution',blocked);monkeypatch.setattr(runner,'evaluate_risk',blocked)
    response=post(a);assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='paper_run_reconciliation_required'
    assert a.db.rows==before;blocked.assert_not_called()


@pytest.mark.parametrize('revoke',['kill_switch','paused'])
def test_pending_authorization_does_not_bypass_current_controls(api,revoke):
    a=api();r=a.client.post(f'/api/mandates/{MID}/risk-evaluate',json={},headers={'Origin':ORIGIN})
    assert r.status_code==200,r.text
    assert r.json()['status']=='APPROVED'
    if revoke=='kill_switch':
        for row in a.db.rows['paper_kill_switch_states']:
            if row['scope']=='SYSTEM':row['active']=True
    else:a.db.rows['trading_mandates'][0]['status']='PAUSED'
    a.session.saved=deepcopy(a.db.rows)
    response=post(a)
    if revoke=='kill_switch':assert response.status_code==200 and response.json()['status']=='RISK_REJECTED',response.text
    else:assert response.status_code==409,response.text
    assert all(n==0 for n in economic_counts(a.db).values())
    assert len(a.db.rows['paper_authorizations'])==1


def test_market_changed_after_risk_does_not_execute(api):
    a=api();before=deepcopy(a.db.rows)
    def changed_clock():
        a.db.rows['market_candles'].pop()
        return a.executed[0]
    a.app.dependency_overrides[runner.execution_clock]=lambda:changed_clock
    response=post(a);assert response.status_code==409,response.text
    assert a.db.rows==before


def test_execution_phase_does_not_re_evaluate_strategy_or_risk(api,monkeypatch):
    a=api()
    from app.risk.paper_source import market
    from app.risk import authorization_service
    from app.risk.integrated import inspection
    def forbidden(*args,**kwargs):raise AssertionError('Execution cannot re-evaluate strategy or Risk')
    def clock_after_risk():
        monkeypatch.setattr(market,'evaluate_strategy',forbidden)
        monkeypatch.setattr(authorization_service,'inspect_risk',forbidden)
        monkeypatch.setattr(inspection,'inspect_risk',forbidden)
        return a.executed[0]
    a.app.dependency_overrides[runner.execution_clock]=lambda:clock_after_risk
    response=post(a);assert response.status_code==200,response.text
    assert response.json()['status']=='PAPER_EXECUTED'


def test_cross_owner_cannot_recover_another_users_economic_fact(api):
    a=api();assert post(a).status_code==200
    before=deepcopy(a.db.rows);a.user.id=uuid4()
    assert post(a).status_code==404
    assert a.db.rows==before


def test_result_is_deeply_frozen(api):
    a=api()
    result=runner.run(a.session,USER,MID,evaluated_at=a.now[0],clock=lambda:a.executed[0])
    with pytest.raises(ValidationError):result.approved_quantity=D('999')
    with pytest.raises(ValidationError):result.execution.quantity=D('999')


def test_four_fresh_hash_seeds_preserve_end_to_end_identities():
    script="""
import sys,json
sys.path.insert(0,'tests')
from test_mandate_paper import seeded,Session,runner,USER,MID,timedelta
outputs=[]
for kind in ('long_profit','short_profit','no_trades'):
    db,at=seeded(kind);session=Session(db)
    result=runner.run(session,USER,MID,evaluated_at=at,clock=lambda:at+timedelta(seconds=1))
    session.commit();outputs.append(result.model_dump(mode='json'))
    if result.status.value=='PAPER_EXECUTED':
        db.rows['trading_mandates'][0]['status']='PAUSED'
        retry=runner.run(session,USER,MID,evaluated_at=at+timedelta(days=2),clock=lambda:at+timedelta(days=2))
        outputs.append(retry.model_dump(mode='json'))
print(json.dumps(outputs,sort_keys=True))
"""
    results=[]
    for seed in ('1','7','42','12345'):
        p=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).parents[1],env={**os.environ,'PYTHONHASHSEED':seed},capture_output=True,text=True,timeout=60)
        assert p.returncode==0,p.stderr
        results.append(p.stdout)
    assert len(set(results))==1
