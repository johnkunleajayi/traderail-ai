"""Real canonical strategy, four Risk gates and registry with isolated SQL doubles.
No physical DB/provider/execution. Transaction/lock statements are inspected here;
real PostgreSQL locking and durability require separate isolated verification.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import json,os,subprocess,sys
from pathlib import Path
import pytest
from sqlalchemy.exc import OperationalError
from app.mandate_risk import service as bridge
from app.risk.paper_source import service as source
from app.execution.registry import repository
from app.risk.paper_switches import load_inventory
from app.risk.vocabulary import KillSwitchScope, RiskOutcome
from app.risk.paper_source.policies import COSTS
from app.execution.registry.codec import encode,decode
from app.strategy_lab.numeric import exact_sum
from app.risk.integrated.models import GateName
from app.mandate_strategy.contracts import MandateEvaluationError
from test_mandate_strategy import make_api, OWNER, MID, PID, ORIGIN, Result
from test_paper_switches import connection as switch_connection

D=Decimal

class Rows(Result):
    def scalar_one_or_none(self):return self.rows[0] if self.rows else None
    def scalar_one(self):assert len(self.rows)==1;return self.rows[0]
    def first(self):return self.rows[0] if self.rows else None
    def one(self):assert len(self.rows)==1;return self.rows[0]
    def scalars(self):return self


@pytest.fixture
def risk_api(make_api,monkeypatch):
    clients=[]
    def make(kind='long_profit'):
        api=make_api(kind);c=api.connection
        c.dialect=SimpleNamespace(name='postgresql')
        c.in_transaction=lambda:True
        c.connection=SimpleNamespace(dbapi_connection=SimpleNamespace(autocommit=False))
        c.get_isolation_level=lambda:'READ COMMITTED'
        c.tables={'paper_risk_initializations':{},'paper_strategy_activations':{},'paper_authorizations':{}}
        c.committed=deepcopy(c.tables);c.active_scope=None;c.missing_scope=None
        original=c.execute
        @contextmanager
        def nested():
            before=deepcopy(c.tables)
            try:yield
            except Exception:c.tables=before;raise
        c.begin_nested=nested
        def execute(query):
            if getattr(query,'is_insert',False):
                name=query.table.name
                assert name in c.tables, 'Economic/unknown write forbidden: '+name
                c.statements.append(query);row=query.compile().params
                key=row.get('registry_identity') or row.get('configuration_identity') or row.get('portfolio_id')
                c.tables[name].setdefault(key,deepcopy(row));return Rows(())
            table=query.get_final_froms()[0].name
            cols=[col.name for col in query.selected_columns];params=query.compile().params
            if table=='users':c.statements.append(query);return Rows([OWNER])
            if table=='paper_portfolios' and cols==['id']:
                c.statements.append(query)
                return Rows([PID] if params.get('user_id_1',OWNER)==OWNER and params.get('id_1',PID)==PID else [])
            if table=='paper_portfolios' and cols==['starting_balance']:c.statements.append(query);return Rows([D('10000')])
            if table in ('paper_positions','paper_accounting_entries','execution_requests') and cols==['portfolio_id']:
                c.statements.append(query);return Rows(())
            if table in c.tables:
                c.statements.append(query)
                data=list(c.tables[table].values())
                for key,value in params.items():
                    column=key.rsplit('_',1)[0]
                    if column in ('user_id','portfolio_id','decision_identity','registry_identity','configuration_identity'):
                        data=[r for r in data if r[column]==value]
                return Rows([r[cols[0]] for r in data] if len(cols)==1 else data)
            return original(query)
        c.execute=execute
        def commit():c.committed=deepcopy(c.tables)
        def rollback():c.tables=deepcopy(c.committed)
        api.db.commit.side_effect=commit;api.db.rollback.side_effect=rollback
        clients.append(api);return api
    monkeypatch.setattr(source,'provision_inactive',lambda *a,**k:None)
    def inventory(c,intent,**kwargs):
        sql,_=switch_connection(intent,active=c.active_scope,missing=c.missing_scope,changed_at=kwargs['observed_at'])
        return load_inventory(sql,intent,**kwargs)
    monkeypatch.setattr(source,'load_inventory',inventory)
    return make


def post(api,body=None,origin=ORIGIN):
    return api.client.post(f'/api/mandates/{MID}/risk-evaluate',json={} if body is None else body,
        headers={} if origin is None else {'Origin':origin})


def test_real_long_to_trade_intent_all_four_gates_and_unconsumed_registry(risk_api):
    api=risk_api();before=deepcopy(api.connection.portfolio)
    response=post(api);assert response.status_code==200,response.text
    result=response.json();assert result['status']=='APPROVED'
    assert result['strategy']['strategy_decision']['decision']=='ENTER_LONG'
    assert len(result['risk']['gates'])==4 and {g['outcome'] for g in result['risk']['gates']}=={'PASS'}
    assert D(result['risk']['modeled_entry_commitment'])<=D('500')
    assert exact_sum((D(result['risk']['modeled_entry_notional']),D(result['risk']['modeled_entry_fee'])))==D(result['risk']['modeled_entry_commitment'])
    rows=api.connection.tables['paper_authorizations'];assert len(rows)==1
    package=repository.load_authorization(api.connection,result['authorization']['registry_identity'],user_id=OWNER)
    assert package.inputs.snapshot.available_cash==D('10000')
    assert package.inputs.snapshot.valuation_identity==result['strategy']['book_identity']
    assert package.inputs.sizing_assumptions.costs is COSTS or package.inputs.sizing_assumptions.costs==COSTS
    assert package.inputs.sizing_assumptions.proposed_quantity is None
    assert package.inputs.sizing_assumptions.constraints.max_entry_cash_commitment==D('500')
    assert package.inputs.intent.entry_context['mandate_provenance']['mandate_identity']==str(MID)
    assert package.inputs.intent.strategy_decision_identity==result['strategy']['strategy_decision']['decision_identity']
    assert decode(encode(package))==package
    assert api.connection.portfolio==before
    assert all(q.table.name in api.connection.tables for q in api.connection.statements if getattr(q,'is_insert',False))
    assert not any('execution_approval_consumptions' in str(q) for q in api.connection.statements)
    assert api.queries[0]._for_update_arg is not None
    assert any(q._for_update_arg is not None for q in api.connection.statements if getattr(q,'is_select',False) and q.get_final_froms()[0].name=='paper_portfolios')
    assert response.headers['cache-control']=='no-store'


@pytest.mark.parametrize('kind',['no_trades','short_profit'])
def test_non_actionable_never_constructs_intent_or_calls_risk(risk_api,monkeypatch,kind):
    api=risk_api(kind)
    blocked=Mock(side_effect=AssertionError('NO_TRADE cannot reach Risk'))
    monkeypatch.setattr(bridge,'assemble',blocked);monkeypatch.setattr(bridge,'evaluate_and_register',blocked)
    response=post(api);assert response.status_code==200,response.text
    result=response.json();assert result['status']=='NO_TRADE' and result['intent_identity'] is None
    assert result['risk'] is result['authorization'] is None
    if kind=='short_profit':assert result['strategy']['strategy_decision']['decision']=='ENTER_SHORT'
    blocked.assert_not_called();assert not api.connection.tables['paper_authorizations']
    assert not any(getattr(q,'is_insert',False) for q in api.connection.statements)


def test_retry_returns_same_authorization_even_after_approval_expiry(risk_api,monkeypatch):
    api=risk_api();first=post(api);assert first.status_code==200,first.text
    original=first.json();tables=deepcopy(api.connection.tables)
    def no_new_risk(*a,**k):raise AssertionError('Identical proposal must reuse authorization')
    monkeypatch.setattr(bridge,'evaluate_and_register',no_new_risk)
    retry=post(api).json();assert retry['authorization']['reused'] is True
    api.connection.at+=timedelta(minutes=5,seconds=1)
    expired=post(api);assert expired.status_code==200,expired.text
    expired=expired.json();assert expired['authorization']['expired'] is True
    for result in (retry,expired):
        assert result['intent_identity']==original['intent_identity']
        assert result['risk']==original['risk']
        assert result['authorization']['registry_identity']==original['authorization']['registry_identity']
    assert api.connection.tables==tables


@pytest.mark.parametrize('scope',list(KillSwitchScope))
def test_existing_safety_gate_rejection_never_registers(risk_api,scope):
    api=risk_api();api.connection.active_scope=scope
    response=post(api);assert response.status_code==200,response.text
    result=response.json();assert result['status']=='REJECTED' and result['authorization'] is None
    assert scope.value+'_KILL_SWITCH_ACTIVE' in result['risk']['blocking_reason_codes']
    assert len(result['risk']['gates'])==4
    assert not api.connection.tables['paper_authorizations']


def test_missing_safety_evidence_fails_closed(risk_api):
    api=risk_api();api.connection.missing_scope=KillSwitchScope.DATA
    response=post(api);assert response.status_code==409,response.text
    assert not api.connection.tables['paper_authorizations']
    assert api.connection.tables==api.connection.committed


@pytest.mark.parametrize('status',['DRAFT','PAUSED','RETIRED'])
def test_ineligible_stops_before_risk_and_writes(risk_api,monkeypatch,status):
    api=risk_api();api.intent.status=status
    blocked=Mock(side_effect=AssertionError('Ineligible cannot reach Risk'))
    monkeypatch.setattr(bridge,'assemble',blocked)
    assert post(api).status_code==409
    blocked.assert_not_called();api.db.connection.assert_not_called()


@pytest.mark.parametrize('body',[{'quantity':'1'},{'price':'100'},{'strategy':'ENTER_LONG'},
    {'risk_profile':'GROWTH'},{'capital_allocation':'9999'},{'balance':'9999'},{'authorization':True}])
def test_http_truth_is_rejected(risk_api,body):
    api=risk_api();assert post(api,body).status_code==422
    api.db.connection.assert_not_called()


@pytest.mark.parametrize('origin',[None,'https://evil.example',ORIGIN+'/'])
def test_origin_enforced(risk_api,origin):
    api=risk_api();assert post(api,origin=origin).status_code==403
    api.db.scalar.assert_not_called()


def test_owner_isolation(risk_api):
    api=risk_api();api.user.id=uuid4()
    assert post(api).status_code==404
    assert not api.connection.tables['paper_authorizations']


def test_anonymous_401():
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.core.config import Settings
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.post(f'/api/mandates/{MID}/risk-evaluate',json={},headers={'Origin':ORIGIN}).status_code==401


def test_registry_failure_rolls_back_every_authority_write(risk_api,monkeypatch):
    api=risk_api()
    original=repository.persist_authorization
    def fail_after_insert(*args,**kwargs):
        original(*args,**kwargs)
        raise OperationalError('private statement',{},Exception('private credentials'))
    monkeypatch.setattr('app.risk.authorization_service.persist_authorization',fail_after_insert)
    response=post(api);assert response.status_code==503,response.text
    assert 'private' not in response.text
    assert all(not rows for rows in api.connection.tables.values())
    api.db.rollback.assert_called_once()


def test_changed_evidence_between_observation_and_risk_rejects(risk_api,monkeypatch):
    api=risk_api();original=bridge.assemble
    def mismatch(*args,**kwargs):
        inputs,decision=original(*args,**kwargs)
        changed=replace(inputs.market_context,data_identity='sha256:'+'f'*64)
        return replace(inputs,market_context=changed),decision
    monkeypatch.setattr(bridge,'assemble',mismatch)
    response=post(api);assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='mandate_risk_context_mismatch'
    assert all(not rows for rows in api.connection.tables.values())


def test_different_allocation_is_a_different_proposal_with_risk_sizing(risk_api):
    api=risk_api();first=post(api).json()
    api.intent.capital_allocation=D('250')
    second=post(api);assert second.status_code==200,second.text
    second=second.json()
    assert second['intent_identity']!=first['intent_identity']
    assert D(second['risk']['modeled_entry_commitment'])<=D('250')
    assert D(second['authorization']['quantity'])<D(first['authorization']['quantity'])
    assert api.connection.portfolio['cash_balance']==D('10000')


def test_new_active_switch_cannot_hide_behind_previous_approval(risk_api):
    api=risk_api();first=post(api);assert first.status_code==200,first.text
    assert first.json()['status']=='APPROVED'
    before=deepcopy(api.connection.tables['paper_authorizations'])
    api.connection.active_scope=KillSwitchScope.SYSTEM
    api.connection.at+=timedelta(seconds=1)
    rejected=post(api);assert rejected.status_code==200,rejected.text
    assert rejected.json()['status']=='REJECTED' and rejected.json()['authorization'] is None
    assert 'SYSTEM_KILL_SWITCH_ACTIVE' in rejected.json()['risk']['blocking_reason_codes']
    assert api.connection.tables['paper_authorizations']==before


def test_missing_current_switch_evidence_blocks_approval_retry(risk_api):
    api=risk_api();assert post(api).json()['status']=='APPROVED'
    before=deepcopy(api.connection.tables)
    api.connection.missing_scope=KillSwitchScope.MARKET
    response=post(api);assert response.status_code==409,response.text
    assert api.connection.tables==before


@pytest.mark.parametrize('field',['portfolio_policy','state_policy','safety_policy'])
def test_missing_mandatory_gate_input_never_approves(risk_api,monkeypatch,field):
    api=risk_api();original=bridge.assemble
    def missing(*args,**kwargs):
        inputs,decision=original(*args,**kwargs)
        return replace(inputs,**{field:None}),decision
    monkeypatch.setattr(bridge,'assemble',missing)
    response=post(api);assert response.status_code==200,response.text
    result=response.json();assert result['status']=='REJECTED'
    assert len(result['risk']['gates'])==4 and result['authorization'] is None
    assert not api.connection.tables['paper_authorizations']


def test_large_allocation_does_not_override_portfolio_concentration(risk_api):
    api=risk_api();api.intent.capital_allocation=D('10000')
    response=post(api);assert response.status_code==200,response.text
    result=response.json();assert result['status']=='REJECTED'
    assert any(g['gate']=='PORTFOLIO_RISK' and g['outcome']=='REJECTED' for g in result['risk']['gates'])
    assert not api.connection.tables['paper_authorizations']


@pytest.mark.parametrize('change',['stale','missing','currency','environment'])
def test_unavailable_market_or_book_never_creates_authorization(risk_api,change):
    api=risk_api()
    if change=='stale':api.connection.at+=timedelta(minutes=10)
    elif change=='missing':api.connection.rows=()
    elif change=='currency':api.connection.portfolio['base_currency']='USD'
    else:api.connection.portfolio['environment']='LIVE'
    response=post(api);assert response.status_code==409,response.text
    assert all(not rows for rows in api.connection.tables.values())


def test_registry_corruption_fails_closed_without_overwriting_existing_fact(risk_api):
    api=risk_api();assert post(api).json()['status']=='APPROVED'
    row=next(iter(api.connection.tables['paper_authorizations'].values()))
    row['canonical_payload']='tampered'
    api.connection.committed=deepcopy(api.connection.tables)
    response=post(api);assert response.status_code==503,response.text
    assert len(api.connection.tables['paper_authorizations'])==1
    assert next(iter(api.connection.tables['paper_authorizations'].values()))['canonical_payload']=='tampered'


def test_equivalent_decimal_allocation_reuses_same_authorization(risk_api):
    api=risk_api();api.intent.capital_allocation=D('500.0');first=post(api).json()
    api.intent.capital_allocation=D('500.00');second=post(api).json()
    assert second['authorization']['registry_identity']==first['authorization']['registry_identity']
    assert len(api.connection.tables['paper_authorizations'])==1


def test_four_fresh_hash_seeds_cover_approved_rejected_no_trade_and_retries():
    script="""
import sys,json
sys.path.insert(0,'tests')
import pytest
import test_mandate_risk as t
patch=pytest.MonkeyPatch()
generator=t.make_api.__wrapped__(patch)
try:
    make=t.risk_api.__wrapped__(next(generator),patch)
    outputs=[]
    for kind in ('long_profit','short_profit','no_trades'):
        api=make(kind)
        result=t.bridge.evaluate(api.db,t.OWNER,t.MID,evaluated_at=api.connection.at)
        api.db.commit()
        outputs.append(result.model_dump(mode='json'))
        if result.status.value=='APPROVED':
            retry=t.bridge.evaluate(api.db,t.OWNER,t.MID,evaluated_at=api.connection.at)
            outputs.append(retry.model_dump(mode='json'))
    api=make();api.connection.active_scope=t.KillSwitchScope.SYSTEM
    outputs.append(t.bridge.evaluate(api.db,t.OWNER,t.MID,evaluated_at=api.connection.at).model_dump(mode='json'))
    print(json.dumps(outputs,sort_keys=True))
finally:
    generator.close();patch.undo()
"""
    results=[]
    for seed in ('1','7','42','12345'):
        completed=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).parents[1],
            env={**os.environ,'PYTHONHASHSEED':seed},capture_output=True,text=True,timeout=60)
        assert completed.returncode==0,completed.stderr
        results.append(completed.stdout)
    assert len(set(results))==1


def test_reduced_snapshot_is_not_a_mandate_budget_workaround(risk_api,monkeypatch):
    api=risk_api();original=bridge.assemble
    def fake_snapshot(*args,**kwargs):
        inputs,decision=original(*args,**kwargs)
        return replace(inputs,snapshot=replace(inputs.snapshot,available_cash=D('500'))),decision
    monkeypatch.setattr(bridge,'assemble',fake_snapshot)
    response=post(api);assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='mandate_risk_context_mismatch'
    assert not api.connection.tables['paper_authorizations']
    assert api.connection.portfolio['cash_balance']==D('10000')
