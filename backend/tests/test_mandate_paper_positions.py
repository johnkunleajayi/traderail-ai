"""Position-aware mandate adapter over real services and the guarded SQL ledger.
No database/provider connections; PostgreSQL locking remains separate verification.
"""
from copy import deepcopy
from datetime import timedelta
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock
import pytest
from app.mandate_paper import positions as management
from app.execution.registry.repository import load_authorization
from app.risk.paper_source.policies import COSTS
from app.risk.vocabulary import KillSwitchScope
from app.strategy_lab.numeric import exact_sum, difference, exact_product
from test_mandate_paper import api, post, economic_counts, USER, MID, D, runner
from test_paper_production_lifecycle import add_candles
from test_integrated_9c_audit import frozen_dataset


def opened(api):
    a=api();r=post(a);assert r.status_code==200,r.text
    assert r.json()['status']=='PAPER_EXECUTED'
    return a,r.json()


def advance(a,kind='long_profit'):
    ds=frozen_dataset(kind);add_candles(a.db,ds.candles)
    a.now[0]=ds.candles[-1].close_time+timedelta(minutes=1)
    a.executed[0]=a.now[0]+timedelta(seconds=1)
    a.session.saved=deepcopy(a.db.rows)


def test_open_position_uses_real_position_aware_hold_not_another_entry(api,monkeypatch):
    a,entry=opened(api);before=deepcopy(a.db.rows)
    no_entry=Mock(side_effect=AssertionError('No entry evaluation with an acquired position'))
    monkeypatch.setattr(runner,'evaluate_risk',no_entry)
    monkeypatch.setattr(management,'registered_response',Mock(side_effect=AssertionError('HOLD cannot authorize')))
    response=post(a);assert response.status_code==200,response.text
    r=response.json();assert r['status']=='POSITION_HELD' and r['strategy_decision']=='NO_ACTION'
    assert r['position_id']==entry['execution']['position_id'] and r['execution'] is None
    assert r['risk_outcome'] is None and a.db.rows==before
    assert economic_counts(a.db)=={t:1 for t in economic_counts(a.db)}
    no_entry.assert_not_called()


def test_real_exit_fresh_risk_canonical_close_pnl_and_durable_retry(api,monkeypatch):
    a,entry=opened(api);advance(a)
    response=post(a);assert response.status_code==200,response.text
    r=response.json();assert r['status']=='POSITION_CLOSED',r
    e=r['execution'];q=D(entry['approved_quantity']);opening=entry['execution']
    assert r['strategy_decision']=='EXIT_LONG' and r['risk_outcome']=='APPROVED'
    assert r['opening_registry_identity']==entry['registry_identity']
    assert r['opening_intent_identity']==entry['intent_identity']
    assert r['position_id']==opening['position_id']==e['position_id']
    assert r['decision_identity']!=entry['decision_identity'] and r['authorization_identity']!=entry['authorization_identity']
    assert e['action']=='EXIT_LONG' and e['position_effect']=='CLOSED'
    assert D(e['quantity'])==q==D(r['approved_quantity'])
    package=load_authorization(a.db,r['registry_identity'],user_id=USER)
    assert package.inputs.sizing_assumptions.costs==COSTS
    assert package.authorization.intent.entry_context['opening_authorization']==entry['registry_identity']
    assert package.authorization.intent.entry_context['paper_position_identity']==r['position_identity']
    assert package.inputs.sizing_assumptions.proposed_quantity==q
    assert len(package.authorization.report.gates)==4
    # Independent accounting oracle: 177 entry reference -> 193 exit reference;
    # spread/2 + slippage = .001 adverse movement, fee .001, no duplicate charges.
    assert D(opening['execution_price'])==D('177.177')
    assert D(e['execution_price'])==D('192.807')
    expected_net=difference(exact_product(q,D('192.807')*D('.999')),exact_product(q,D('177.177')*D('1.001')))
    assert D(e['gross_realized_pnl'])==exact_product(q,D('16'))
    assert D(e['net_realized_pnl'])==expected_net
    assert D(e['cash_after'])==exact_sum((D('10000'),expected_net))
    assert a.db.rows['paper_positions'][0]['status']=='CLOSED'
    assert a.db.rows['paper_positions'][0]['quantity']==0
    assert all(n==2 for t,n in economic_counts(a.db).items() if t!='paper_positions')
    before=deepcopy(a.db.rows);a.now[0]+=timedelta(days=2);a.executed[0]=a.now[0]
    monkeypatch.setattr(management,'assemble_close',Mock(side_effect=AssertionError('Recover closed fact without strategy')))
    monkeypatch.setattr(runner,'evaluate_risk',Mock(side_effect=AssertionError('No new entry')))
    again=post(a);assert again.status_code==200,again.text
    assert again.json()['status']=='POSITION_CLOSED' and again.json()['reused'] is True
    assert again.json()['execution']==e and a.db.rows==before


def test_exit_risk_rejection_does_not_execute(api,monkeypatch):
    a,_=opened(api);advance(a)
    for row in a.db.rows['paper_kill_switch_states']:
        if row['scope']=='SYSTEM':row['active']=True
    a.session.saved=deepcopy(a.db.rows);before=deepcopy(a.db.rows)
    blocked=Mock(side_effect=AssertionError('Rejected exit cannot execute'))
    monkeypatch.setattr(runner,'run_paper_execution',blocked)
    response=post(a);assert response.status_code==200,response.text
    assert response.json()['status']=='EXIT_RISK_REJECTED'
    assert response.json()['execution'] is None and response.json()['risk_outcome']=='REJECTED'
    assert a.db.rows==before and len(a.db.rows['paper_authorizations'])==1
    blocked.assert_not_called()


@pytest.mark.parametrize('problem',['expired','accounting_failure','market_changed','wrong_authorization'])
def test_exit_failure_rolls_back_new_authority_and_economics(api,monkeypatch,problem):
    a,entry=opened(api);advance(a);before=deepcopy(a.db.rows)
    if problem=='expired':a.executed[0]=a.now[0]+timedelta(minutes=6)
    elif problem=='accounting_failure':a.db.fail_table='paper_accounting_entries'
    elif problem=='market_changed':
        def changed():
            a.db.rows['market_candles'].pop()
            return a.executed[0]
        a.app.dependency_overrides[runner.execution_clock]=lambda:changed
    else:
        original=management.load_authorization
        def wrong(c,key,**kw):
            package=original(c,key,**kw)
            if package.authorization.intent.entry_context.get('paper_action')=='EXIT_LONG':
                return original(c,entry['registry_identity'],**kw)
            return package
        monkeypatch.setattr(management,'load_authorization',wrong)
    response=post(a);assert response.status_code in (409,503),response.text
    assert a.db.rows==before and a.db.rows['paper_positions'][0]['status']=='OPEN'


@pytest.mark.parametrize('problem',['stale','missing','USD','LIVE','owner','wrong_position_owner','wrong_portfolio','instrument','strategy','quantity','opening_fill','paused'])
def test_position_context_fails_closed_without_economic_mutation(api,problem):
    a,_=opened(api);advance(a)
    if problem=='stale':a.now[0]+=timedelta(hours=2);a.executed[0]=a.now[0]
    elif problem=='missing':a.db.rows['market_candles']=[]
    elif problem=='USD':a.db.rows['paper_portfolios'][0]['base_currency']='USD'
    elif problem=='LIVE':a.db.rows['paper_portfolios'][0]['environment']='LIVE'
    elif problem=='owner':a.user.id=__import__('uuid').uuid4()
    elif problem=='wrong_position_owner':a.db.rows['paper_positions'][0]['user_id']=__import__('uuid').uuid4()
    elif problem=='wrong_portfolio':a.db.rows['paper_positions'][0]['portfolio_id']=__import__('uuid').uuid4()
    elif problem=='instrument':a.db.rows['paper_positions'][0]['instrument_id']=__import__('uuid').uuid4()
    elif problem=='strategy':a.db.rows['paper_positions'][0]['strategy_version']='untrusted'
    elif problem=='quantity':a.db.rows['paper_positions'][0]['opening_quantity']+=D('1')
    elif problem=='opening_fill':a.db.rows['paper_positions'][0]['opening_fill_identity']='sha256:'+'f'*64
    else:a.db.rows['trading_mandates'][0]['status']='PAUSED'
    a.session.saved=deepcopy(a.db.rows);before=deepcopy(a.db.rows)
    r=post(a)
    if problem=='paused':assert r.status_code==200 and r.json()['reused'] is True,r.text
    else:assert r.status_code in (404,409),r.text
    assert a.db.rows==before


@pytest.mark.parametrize('problem',['missing_receipt','unknown_order','consumption_only','extra_fill'])
def test_ambiguous_close_requires_reconciliation_no_new_action(api,monkeypatch,problem):
    a,_=opened(api);advance(a);response=post(a);assert response.status_code==200,response.text
    if problem=='missing_receipt':a.db.rows['paper_accounting_entries'].pop()
    elif problem=='unknown_order':a.db.rows['execution_orders'][-1]['state']='UNKNOWN'
    elif problem=='extra_fill':
        row=deepcopy(a.db.rows['execution_fills'][-1]);row['fill_identity']='sha256:'+'f'*64;a.db.rows['execution_fills'].append(row)
    else:
        a.db.rows['paper_authorized_commands'].pop();a.db.rows['paper_accounting_entries'].pop()
    a.session.saved=deepcopy(a.db.rows);before=deepcopy(a.db.rows)
    monkeypatch.setattr(management,'assemble_close',Mock(side_effect=AssertionError('Unknown cannot resubmit')))
    r=post(a);assert r.status_code==409,r.text
    assert r.json()['detail']['code']=='paper_run_reconciliation_required'
    assert a.db.rows==before

@pytest.mark.parametrize('body',[{'price':'1'},{'quantity':'1'},{'pnl':'999'},
    {'strategy_outcome':'EXIT_LONG'},{'risk_outcome':'APPROVED'}])
def test_open_position_command_cannot_accept_financial_truth(api,body):
    a,_=opened(api);before=deepcopy(a.db.rows)
    response=post(a,body);assert response.status_code==422,response.text
    assert a.db.rows==before


def test_close_execution_phase_cannot_evaluate_strategy_or_risk(api,monkeypatch):
    a,_=opened(api);advance(a)
    from app.risk.paper_source import market
    from app.risk import authorization_service
    def forbidden(*args,**kw):raise AssertionError('Only strategy/Risk may decide before execution')
    def clock():
        monkeypatch.setattr(market,'evaluate_strategy',forbidden)
        monkeypatch.setattr(authorization_service,'inspect_risk',forbidden)
        return a.executed[0]
    a.app.dependency_overrides[runner.execution_clock]=lambda:clock
    response=post(a);assert response.status_code==200,response.text
    assert response.json()['status']=='POSITION_CLOSED'


def test_management_acquires_database_row_locks(api):
    a,_=opened(api);advance(a)
    start=len(a.db.statements);response=post(a);assert response.status_code==200,response.text
    locked={s.get_final_froms()[0].name for s in a.db.statements[start:]
        if getattr(s,'_for_update_arg',None) is not None}
    assert {'trading_mandates','paper_portfolios','paper_positions'}<=locked
    # Statement-level proof only: the ledger does not model PostgreSQL contention.


def test_closed_position_cannot_be_reentered_on_later_market_signal(api,monkeypatch):
    a,_=opened(api);advance(a);closed=post(a);assert closed.status_code==200,closed.text
    before=deepcopy(a.db.rows)
    monkeypatch.setattr(runner,'evaluate_risk',Mock(side_effect=AssertionError('No automatic re-entry')))
    a.db.rows['trading_mandates'][0]['status']='RETIRED';a.session.saved=deepcopy(a.db.rows)
    r=post(a);assert r.status_code==200,r.text
    assert r.json()['status']=='POSITION_CLOSED' and r.json()['reused'] is True
    assert a.db.rows['paper_portfolios']==before['paper_portfolios']
    assert economic_counts(a.db)==economic_counts(type('DB',(),{'rows':before})())


def test_four_fresh_hash_seeds_preserve_hold_close_and_recovery():
    import os,sys,subprocess,json
    from pathlib import Path
    script="""
import sys,json
sys.path.insert(0,'tests')
from test_mandate_paper import seeded,Session,runner,USER,MID,timedelta
from test_integrated_9c_audit import frozen_dataset
from test_paper_production_lifecycle import add_candles
out=[];db,at=seeded();session=Session(db)
for instant in (at,at):
    result=runner.run(session,USER,MID,evaluated_at=instant,clock=lambda:instant+timedelta(seconds=1))
    session.commit();out.append(result.model_dump(mode='json'))
ds=frozen_dataset('long_profit');add_candles(db,ds.candles);at=ds.candles[-1].close_time+timedelta(minutes=1)
for instant in (at,at+timedelta(days=2)):
    result=runner.run(session,USER,MID,evaluated_at=instant,clock=lambda:instant+timedelta(seconds=1))
    session.commit();out.append(result.model_dump(mode='json'))
assert [x['status'] for x in out]==['PAPER_EXECUTED','POSITION_HELD','POSITION_CLOSED','POSITION_CLOSED']
print(json.dumps(out,sort_keys=True))
"""
    results=[]
    for seed in ('1','7','42','12345'):
        p=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).parents[1],
            env={**os.environ,'PYTHONHASHSEED':seed},capture_output=True,text=True,timeout=90)
        assert p.returncode==0,p.stderr
        results.append(p.stdout)
    assert len(set(results))==1


def test_unexpected_held_context_proposal_is_not_reinterpreted(api,monkeypatch):
    from types import SimpleNamespace
    from app.strategies.decisions import DecisionType
    a,_=opened(api);before=deepcopy(a.db.rows)
    monkeypatch.setattr(management,'assemble_close',lambda *args,**kw:(None,SimpleNamespace(decision=DecisionType.ENTER_SHORT)))
    response=post(a);assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='paper_run_reconciliation_required'
    assert a.db.rows==before
