"""Real engines/services over an isolated SQL ledger double, not PostgreSQL proof.

Synthetic causal candles reuse the established 9C audit scenario unchanged.
The approval fixture explicitly proposes one unit to the unchanged Risk gates;
production maximum-size selection and thresholds remain unchanged. No production
market evidence is written or changed, and no provider is invoked.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
from app.auth.dependencies import get_current_user
from app.execution.api.dependencies import execution_time,paper_transactions
from app.execution.paper.production import ProductionPaperCommandSource
from app.execution.api.service import run_paper_execution,PaperAPIError
from app.execution.api.schemas import PaperExecutionRequest
from app.execution.request import ExecutionRequest
from app.execution.paper.book import capture_book
from app.execution.registry.repository import load_authorization
from app.execution.registry.models import authorizations,commands
from app.risk.paper_source.service import assemble,registered_response
from app.risk.paper_source.close import assemble_close,history_state
from app.risk.paper_source import market as risk_market
from app.risk.authorization_service import evaluate_and_register
from app.execution.registry.contracts import ExecutionEvidenceReferences
from app.risk.integrated.inspection import inspect_risk
from app.risk.vocabulary import RiskOutcome
from test_integrated_9c_audit import frozen_dataset
from paper_ledger_double import Ledger

D=Decimal
USER=UUID(int=2);PORT=UUID(int=3);INSTRUMENT=UUID(int=1)
ORIGIN='http://localhost:5173'


def add_candles(db,rows):
    for n,candle in enumerate(rows):
        if any(r['open_time']==candle.open_time for r in db.rows['market_candles']):continue
        row={name:getattr(candle,name) for name in candle.__dataclass_fields__}
        row['retrieved_at']=candle.close_time+timedelta(milliseconds=1)
        row['created_at']=row['updated_at']=row['retrieved_at']
        row['id']=UUID(int=1000+int(candle.open_time.timestamp())//3600)
        db.rows['market_candles'].append(row)


def ready():
    db=Ledger();ds=frozen_dataset('open_long');at=ds.candles[-1].close_time+timedelta(minutes=1)
    db.rows['users']=[dict(id=USER,status='active',google_id='paper-fixture',email='paper@example.invalid',
        name='Paper Fixture',avatar_url=None,country=None,base_currency='USD',created_at=ds.warmup_start,updated_at=ds.warmup_start)]
    db.rows['paper_portfolios']=[dict(id=PORT,user_id=USER,environment='PAPER',base_currency='USDT',
        starting_balance=D('10000'),cash_balance=D('10000'),status='active',
        created_at=ds.warmup_start,updated_at=ds.warmup_start)]
    db.rows['assets']=[dict(id=UUID(int=n),symbol=symbol,name=symbol,asset_type='CRYPTO',status='active',
        created_at=ds.warmup_start,updated_at=ds.warmup_start) for n,symbol in ((10,'BTC'),(11,'USDT'))]
    db.rows['instruments']=[dict(id=INSTRUMENT,canonical_symbol='BTC/USDT',market_type='SPOT',status='active',
        base_asset_id=UUID(int=10),quote_asset_id=UUID(int=11),price_precision=8,quantity_precision=8,
        created_at=ds.warmup_start,updated_at=ds.warmup_start)]
    add_candles(db,ds.candles)
    inputs,decision=assemble(db,USER,at)
    assert decision.decision.value=='ENTER_LONG' and inputs is not None
    # Explicit synthetic Risk proposal, not a production resizing rule.
    inputs=replace(inputs,sizing_assumptions=replace(inputs.sizing_assumptions,proposed_quantity=D('1')))
    refs=ExecutionEvidenceReferences(inputs.market_context.data_identity,inputs.market_context.context_identity,
        inputs.sizing_assumptions.costs.costs_identity,inputs.snapshot.valuation_identity)
    report,package=evaluate_and_register(db,inputs,user_id=USER,evidence_references=refs)
    assert report.decision.outcome is RiskOutcome.APPROVED
    request=ExecutionRequest(package.authorization,'PAPER_INTERNAL',at)
    return db,inputs,package,request,at+timedelta(seconds=1)


def payload(request):
    return PaperExecutionRequest(intent_identity=request.intent_identity,decision_identity=request.risk_decision_identity,
        request_identity=request.request_identity)


def execute(db,r,at):
    return run_paper_execution(ProductionPaperCommandSource(),db.begin,USER,payload(r),at)


def close_inputs(db):
    ds=frozen_dataset('long_profit');add_candles(db,ds.candles)
    at=ds.candles[-1].close_time+timedelta(minutes=1)
    pos=db.rows['paper_positions'][0]
    inputs,decision=assemble_close(db,USER,pos['position_id'],at)
    assert decision.decision.value=='EXIT_LONG' and inputs is not None
    return inputs,decision,at


def authorize_close(db):
    i,d,at=close_inputs(db)
    response=registered_response(db,i,d,USER)
    assert response['status']=='APPROVED'
    row=next(r for r in db.rows['paper_authorizations'] if r['decision_identity']==response['decision_identity'])
    package=load_authorization(db,row['registry_identity'],user_id=USER)
    return i,package,ExecutionRequest(package.authorization,'PAPER_INTERNAL',at),at+timedelta(seconds=1)


def test_real_source_registry_execution_and_full_close_oracle():
    db,i,p,r,at=ready()
    opened=execute(db,r,at)
    assert opened.action=='ENTER_LONG' and opened.quantity==D('1')
    assert opened.execution_price==D('178.178') and opened.fee==D('.178178')
    assert opened.cash_after==D('9821.643822')
    assert len(db.rows['execution_approval_consumptions'])==len(db.rows['execution_fills'])==len(db.rows['paper_positions'])==1
    again=execute(db,r,at+timedelta(days=1))
    assert again==opened and db.rows['paper_portfolios'][0]['cash_balance']==opened.cash_after
    ci,cp,cr,ct=authorize_close(db)
    assert cr.quantity==r.quantity==db.rows['paper_positions'][0]['quantity']
    assert cr.intent_identity!=r.intent_identity and cr.risk_decision_identity!=r.risk_decision_identity
    assert ci.intent.entry_context['paper_action']=='EXIT_LONG'
    closed=execute(db,cr,ct)
    assert ci.intent.reference_price==D('193')
    assert closed.action=='EXIT_LONG' and closed.execution_price==D('192.807')
    assert closed.gross_realized_pnl==D('15') and closed.net_realized_pnl==D('14.258015')
    assert closed.cash_after==D('10014.258015')
    assert execute(db,cr,ct+timedelta(days=1))==closed
    assert len(db.rows['execution_approval_consumptions'])==len(db.rows['execution_fills'])==len(db.rows['paper_accounting_entries'])==2
    assert db.rows['paper_positions'][0]['status']=='CLOSED' and db.rows['paper_positions'][0]['quantity']==0


@pytest.mark.parametrize('problem',['expired','owner','quantity_identity','changed_cash','usd','missing_market','changed_market','wrong_portfolio'])
def test_first_execution_fail_closed_without_partial_economic_artifacts(problem,monkeypatch):
    db,i,p,r,at=ready();user=USER;body=payload(r)
    if problem=='expired':at=p.authorization.decision.approval_expires_at
    elif problem=='owner':user=UUID(int=999)
    elif problem=='quantity_identity':body=body.model_copy(update={'request_identity':'sha256:'+'e'*64})
    elif problem=='changed_cash':db.rows['paper_portfolios'][0]['cash_balance']-=D('1')
    elif problem=='usd':db.rows['paper_portfolios'][0]['base_currency']='USD'
    elif problem=='missing_market':db.rows['market_candles'].pop()
    elif problem=='changed_market':db.rows['market_candles'][-1]['close']+=D('.01')
    else:db.rows['paper_portfolios'][0]['user_id']=UUID(int=998)
    before=db.rows['paper_portfolios'][0]['cash_balance']
    with pytest.raises(PaperAPIError):run_paper_execution(ProductionPaperCommandSource(),db.begin,user,body,at)
    assert db.rows['paper_portfolios'][0]['cash_balance']==before
    assert all(not db.rows[t] for t in ('paper_authorized_commands','execution_requests','execution_approval_consumptions',
        'execution_orders','execution_fills','paper_positions','paper_accounting_entries'))


def test_economic_failure_rolls_back_command_consumption_fill_and_cash():
    db,i,p,r,at=ready();db.fail_table='paper_accounting_entries'
    with pytest.raises(PaperAPIError):execute(db,r,at)
    assert db.rows['paper_portfolios'][0]['cash_balance']==D('10000')
    assert all(not db.rows[t] for t in ('paper_authorized_commands','execution_requests','execution_approval_consumptions',
        'execution_orders','execution_fills','paper_positions','paper_accounting_entries'))


def test_consumed_approval_without_receipt_is_unknown_and_not_resubmitted():
    db,i,p,r,at=ready()
    db.rows['execution_approval_consumptions'].append({'decision_identity':r.risk_decision_identity,
        'action_identity':r.action_identity,'consumption_identity':'sha256:'+'f'*64})
    with pytest.raises(PaperAPIError):execute(db,r,at)
    assert not db.rows['paper_accounting_entries'] and not db.rows['paper_positions']
    assert not db.rows['paper_authorized_commands']


def test_no_entry_authorization_reuse_for_close_and_no_exit_on_no_action():
    db,i,p,r,at=ready();opened=execute(db,r,at)
    # Unchanged entry-era analytics do not justify a new EXIT_LONG.
    inputs,decision=assemble_close(db,USER,opened.position_id,at+timedelta(seconds=1))
    assert inputs is None and decision.decision.value!='EXIT_LONG'
    assert len(db.rows['paper_authorizations'])==1
    assert execute(db,r,at+timedelta(seconds=2))==opened
    assert db.rows['paper_positions'][0]['status']=='OPEN'


def test_execution_never_evaluates_strategy(monkeypatch):
    db,i,p,r,at=ready()
    def forbidden(*a,**k):raise AssertionError('Execution must not evaluate strategy')
    monkeypatch.setattr(risk_market,'evaluate_strategy',forbidden)
    monkeypatch.setattr(risk_market,'calculate_features',forbidden)
    execute(db,r,at)


def test_rejected_or_no_trade_has_no_executable_registry_record():
    db,i,p,r,at=ready();db.rows['paper_authorizations'].clear()
    with pytest.raises(PaperAPIError):execute(db,r,at)
    assert not db.rows['execution_requests']


def test_http_uses_production_source_and_rejects_financial_truth():
    db,i,p,r,at=ready();app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
    user=SimpleNamespace(id=USER,status='active')
    app.dependency_overrides[get_current_user]=lambda:user
    app.dependency_overrides[paper_transactions]=lambda:db.begin
    app.dependency_overrides[execution_time]=lambda:at
    with TestClient(app) as client:
        for field in ('quantity','price','fee','spread','slippage','balance','pnl','approval'):
            body=payload(r).model_dump();body[field]='1'
            assert client.post('/api/paper/executions',json=body,headers={'Origin':ORIGIN}).status_code==422
        response=client.post('/api/paper/executions',json=payload(r).model_dump(),headers={'Origin':ORIGIN})
        assert response.status_code==200 and response.json()['cash_after']=='9821.6438220'
        assert response.json()['currency']=='USDT'


@pytest.mark.parametrize('missing',['initialization','receipt','origin','history'])
def test_close_requires_complete_authoritative_history(missing):
    db,i,p,r,at=ready();execute(db,r,at)
    ds=frozen_dataset('long_profit');add_candles(db,ds.candles)
    close_at=ds.candles[-1].close_time+timedelta(minutes=1)
    if missing=='initialization':db.rows['paper_risk_initializations'].clear()
    elif missing=='receipt':db.rows['paper_accounting_entries'].clear()
    elif missing=='origin':db.rows['paper_authorizations'].clear()
    else:
        opened=db.rows['paper_positions'][0]['opened_at']
        db.rows['market_candles']=[c for c in db.rows['market_candles'] if c['open_time']>opened]
    from app.risk.paper_source.market import PaperEvidenceUnavailable
    with pytest.raises((PaperEvidenceUnavailable,ValueError)):
        assemble_close(db,USER,db.rows['paper_positions'][0]['position_id'],close_at)
    assert db.rows['paper_positions'][0]['status']=='OPEN'
    assert db.rows['paper_portfolios'][0]['cash_balance']==D('9821.643822')


def test_duplicate_waiter_recovers_winner_after_portfolio_lock(monkeypatch):
    # Deterministic interleaving, not a claim of PostgreSQL connection concurrency.
    db,i,p,r,at=ready()
    import app.execution.paper.production as production
    capture=production.capture_book
    winner=[]
    def after_wait(*args,**kwargs):
        monkeypatch.setattr(production,'capture_book',capture)
        winner.append(execute(db,r,at))
        return capture(*args,**kwargs)
    monkeypatch.setattr(production,'capture_book',after_wait)
    assert execute(db,r,at)==winner[0]
    assert len(db.rows['execution_fills'])==len(db.rows['paper_accounting_entries'])==1
    assert db.rows['paper_portfolios'][0]['cash_balance']==D('9821.643822')


def test_risk_snapshot_is_bound_to_exact_book_not_an_unrelated_valuation():
    db,i,p,r,at=ready()
    assert i.snapshot.valuation_identity==capture_book(db,PORT,lock=True).state_identity


def test_close_http_boundary_requires_operator_origin_and_identity_only():
    from app.risk.api.trusted import get_trusted_risk_source
    from app.risk.api.dependencies import evaluation_time
    source=SimpleNamespace(evaluate_close=Mock(return_value={'status':'NO_TRADE'}))
    app=create_app(Settings(_env_file=None,frontend_url=ORIGIN,risk_inspector_user_ids=(USER,)))
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=USER,status='active')
    app.dependency_overrides[get_trusted_risk_source]=lambda:source
    with TestClient(app) as client:
        body={'position_id':str(UUID(int=100))}
        assert client.post('/api/risk/paper-close-evaluations',json=body).status_code==403
        assert client.post('/api/risk/paper-close-evaluations',json={**body,'quantity':'1'},headers={'Origin':ORIGIN}).status_code==422
        response=client.post('/api/risk/paper-close-evaluations',json=body,headers={'Origin':ORIGIN})
        assert response.status_code==200 and response.json()=={'status':'NO_TRADE'}
        assert response.headers['cache-control']=='no-store'
        assert source.evaluate_close.call_count==1
