"""HTTP/transaction-adapter tests with explicit server fixtures, never a live DB.

11D's PostgreSQL atomicity/concurrency proof remains separate. The double here
tests transport, rollback propagation and exact retry forwarding, not SQL locks.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from app.main import create_app
from app.core.config import Settings
from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.execution.api.dependencies import execution_time, paper_transactions
from app.execution.api.trusted import get_trusted_paper_source
from app.execution.api import service
from app.execution.paper import PaperEconomicResult, PaperExecutionError
from app.execution.paper.validation import reconcile
from app.execution.paper.accounting import cash_after, full_close
from test_paper_execution import command_for, empty_book, position_row, calc, D, AT, ID
from app.risk import Direction

URL='/api/paper/executions'
ORIGIN='http://localhost:5173'


@pytest.fixture
def api(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Provider/network/ingestion forbidden')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',forbidden)
    from app.market_data import historical_service, candle_store
    monkeypatch.setattr(historical_service,'ingest_historical_candles',forbidden)
    monkeypatch.setattr(candle_store,'persist_candles',forbidden)
    book=empty_book();command=command_for(book)
    owner=SimpleNamespace(id=book.user_id,status='active')
    app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
    app.dependency_overrides[get_session]=lambda:Mock()
    app.dependency_overrides[get_current_user]=lambda:owner
    now=[AT];app.dependency_overrides[execution_time]=lambda:now[0]
    source=Mock();source.resolve.return_value=command
    app.dependency_overrides[get_trusted_paper_source]=lambda:source
    state={'book':book,'receipts':{},'mutations':0,'commits':0,'rollbacks':0}
    @contextmanager
    def begin():
        snapshot=dict(state)
        snapshot['receipts']=dict(state['receipts'])
        try:yield state
        except BaseException:
            state.clear();state.update(snapshot);state['rollbacks']+=1
            raise
        else:state['commits']+=1
    transaction=Mock(side_effect=begin)
    app.dependency_overrides[paper_transactions]=lambda:transaction
    def economic_double(connection,command,*,as_of):
        r=command.request
        if r.request_identity in state['receipts']:
            previous,saved=state['receipts'][r.request_identity]
            if previous!=command.command_identity:raise PaperExecutionError('Conflicting economic retry')
            return saved
        reconcile(command,state['book'],as_of)
        leg=calc(command).leg
        before=state['book'];cash=cash_after(before.cash_balance,leg)
        close=r.action.value=='EXIT_LONG'
        pos=before.positions[0] if close else position_row(command)
        gross,net=full_close(pos,leg) if close else (None,None)
        result=PaperEconomicResult(r.request_identity,command.command_identity,r.intent_identity,
            r.risk_decision_identity,r.order_identity,ID,before.state_identity,pos['position_id'],
            r.action.value,r.quantity,leg.modeled_execution_price,leg.gross_notional,leg.fee,
            before.cash_balance,cash,pos['entry_cost_basis'] if close else None,
            pos['entry_fee'] if close else None,gross,net,as_of)
        state['book']=empty_book(cash_balance=cash,positions=() if close else (pos,),updated_at=as_of,
            accounting_identities=before.accounting_identities+(result.result_identity,))
        state['receipts'][r.request_identity]=(command.command_identity,result)
        state['mutations']+=1
        return result
    executor=Mock(side_effect=economic_double)
    monkeypatch.setattr(service,'execute_paper',executor)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client,app=app,owner=owner,source=source,command=command,
            state=state,now=now,transaction=transaction,executor=executor)


def body(command):
    r=command.request
    return dict(intent_identity=r.intent_identity,decision_identity=r.risk_decision_identity,request_identity=r.request_identity)


def post(api,payload=None,origin=ORIGIN):
    return api.client.post(URL,json=body(api.command) if payload is None else payload,
        headers={} if origin is None else {'Origin':origin})


def test_authenticated_entry_exact_decimals_and_service_transaction(api):
    r=post(api);assert r.status_code==200
    x=r.json();assert x['status']=='COMPLETED' and x['environment']=='PAPER'
    assert x['position_effect']=='OPENED' and x['action']=='ENTER_LONG'
    assert D(x['execution_price'])==D('102') and D(x['fee'])==D('1.02')
    saved=api.state['receipts'][api.command.request.request_identity][1]
    assert x['cash_after']==format(saved.cash_after,'f') and D(x['cash_after'])==D('9896.98')
    assert isinstance(x['quantity'],str)
    assert x['gross_realized_pnl'] is None and x['executed_at'].endswith('Z')
    assert api.state['commits']==api.state['mutations']==1
    api.executor.assert_called_once_with(api.state,api.command,as_of=AT)
    api.source.resolve.assert_called_once_with(user_id=api.owner.id,**body(api.command),evaluated_at=AT)
    assert r.headers['cache-control']=='no-store' and r.headers['referrer-policy']=='no-referrer'


def test_exact_retry_after_expiry_returns_same_fact_without_second_mutation(api):
    first=post(api);api.now[0]=AT+timedelta(days=1)
    retry=post(api)
    assert retry.status_code==200 and retry.json()==first.json()
    assert api.state['mutations']==len(api.state['receipts'])==1
    assert api.state['book'].cash_balance==D('9896.98')


def test_changed_command_under_same_request_conflicts(api):
    assert post(api).status_code==200
    api.source.resolve.return_value=replace(api.command,executed_at=AT+timedelta(seconds=1))
    r=post(api);assert r.status_code==409 and r.json()['detail']['code']=='paper_execution_conflict'
    assert api.state['mutations']==1 and api.state['book'].cash_balance==D('9896.98')


def test_full_long_close_uses_existing_accounting_result(api):
    assert post(api).status_code==200
    api.now[0]=AT+timedelta(seconds=1)
    close=command_for(api.state['book'],price='120',stop='108',close=ID,at=api.now[0],nonce='sha256:'+'e'*64)
    api.source.resolve.return_value=close
    r=post(api,body(close));assert r.status_code==200
    assert r.json()['position_effect']=='CLOSED' and r.json()['action']=='EXIT_LONG'
    assert D(r.json()['gross_realized_pnl'])==D('20')
    assert D(r.json()['net_realized_pnl'])==D('13.404')
    assert D(r.json()['cash_after'])==D('10013.404')
    assert not api.state['book'].positions
    assert post(api,body(close)).json()==r.json() and api.state['mutations']==2


def test_anonymous_is_401_without_resolution(api):
    del api.app.dependency_overrides[get_current_user]
    assert post(api).status_code==401
    api.source.resolve.assert_not_called();api.transaction.assert_not_called()


@pytest.mark.parametrize('origin',[None,'http://evil.invalid',ORIGIN+'/','http://localhost:5173.evil.invalid'])
def test_exact_origin_required(api,origin):
    assert post(api,origin=origin).status_code==403
    api.source.resolve.assert_not_called();api.transaction.assert_not_called()


def test_inactive_user_is_401(api):
    api.owner.status='disabled'
    assert post(api).status_code==401
    api.source.resolve.assert_not_called()


def test_ownership_isolation_before_transaction(api):
    api.owner.id=UUID(int=900)
    assert post(api).status_code==404
    api.transaction.assert_not_called()


@pytest.mark.parametrize('field',['intent_identity','decision_identity','request_identity'])
def test_identity_mismatch_is_unavailable(api,field):
    payload=body(api.command);payload[field]='sha256:'+'f'*64
    assert post(api,payload).status_code==404
    api.transaction.assert_not_called()


def test_short_is_rejected_before_economic_service(api):
    short=command_for(empty_book(),direction=Direction.SHORT,stop='110')
    api.source.resolve.return_value=short
    assert post(api,body(short)).status_code==409
    api.transaction.assert_not_called();api.executor.assert_not_called()


@pytest.mark.parametrize('field',['quantity','cash_balance','portfolio_id','user_id','position','market','authorization','environment','costs','executed_at'])
def test_client_financial_authority_is_forbidden(api,field):
    payload=body(api.command);payload[field]='untrusted'
    assert post(api,payload).status_code==422
    api.source.resolve.assert_not_called();api.transaction.assert_not_called()


def test_missing_or_malformed_identity(api):
    assert post(api,{}).status_code==422
    payload=body(api.command);payload['intent_identity']='not-an-identity'
    assert post(api,payload).status_code==422


def test_default_evidence_source_is_fail_closed(api):
    del api.app.dependency_overrides[get_trusted_paper_source]
    r=post(api);assert r.status_code==503
    assert r.json()['detail']['code']=='trusted_paper_evidence_unavailable'
    api.transaction.assert_not_called()


def test_source_failure_is_sanitized_without_transaction(api):
    api.source.resolve.side_effect=RuntimeError('private source credentials')
    r=post(api)
    assert r.status_code==503 and 'private' not in r.text
    api.transaction.assert_not_called()


@pytest.mark.parametrize('value',[None,object()])
def test_unknown_trusted_command_is_fail_closed(api,value):
    api.source.resolve.return_value=value
    assert post(api).status_code==503
    api.transaction.assert_not_called()


def test_expired_approval_rejects_without_mutation(api):
    api.now[0]=api.command.request.authorization.decision.approval_expires_at
    r=post(api);assert r.status_code==409
    assert not api.state['receipts'] and api.state['mutations']==0 and api.state['rollbacks']==1


def test_stale_market_rejects_without_mutation(api):
    api.source.resolve.return_value=replace(api.command,market=replace(api.command.market,
        priced_at=AT-timedelta(days=1)))
    assert post(api).status_code==409
    assert api.state['mutations']==0 and not api.state['receipts']


@pytest.mark.parametrize('message',[
    'Insufficient PAPER cash; borrowing/resizing is forbidden',
    'Approved snapshot no longer matches authoritative Paper book',
    'No exact open LONG position to close',
    'Full close only; oversell and partial reduction unsupported',
    'Approval/action already reserved without an economic receipt; explicit recovery required',
])
def test_domain_rejection_rolls_back_and_is_sanitized(api,message):
    def fail(*a,**kw):
        api.state['mutations']=99
        raise PaperExecutionError(message)
    api.executor.side_effect=fail
    r=post(api);assert r.status_code==409
    assert message not in r.text
    assert api.state['mutations']==0 and api.state['rollbacks']==1


def test_storage_failure_cannot_return_success(api):
    api.executor.side_effect=SQLAlchemyError('secret database credentials')
    r=post(api);assert r.status_code==503 and 'secret' not in r.text
    assert api.state['rollbacks']==1


def test_commit_failure_cannot_return_success(api):
    assert post(api).status_code==200
    saved=api.state['receipts'][api.command.request.request_identity][1]
    @contextmanager
    def failing_commit():
        yield api.state
        raise SQLAlchemyError('secret commit failure')
    api.app.dependency_overrides[paper_transactions]=lambda:failing_commit
    api.executor.side_effect=None
    api.executor.return_value=saved
    r=post(api)
    assert r.status_code==503 and 'secret' not in r.text
    assert api.state['mutations']==1


@pytest.mark.parametrize('message,code,status',[
    ('Insufficient PAPER cash','insufficient_paper_cash',409),
    ('Approval is future-dated or expired','paper_authorization_expired',409),
    ('Market evidence is expired or stale','paper_evidence_invalid',409),
    ('Stale evidence','paper_evidence_invalid',409),
    ('Conflicting economic retry','paper_execution_conflict',409),
    ('Paper execution transaction failed','paper_storage_unavailable',503),
])
def test_stable_domain_error_codes(api,message,code,status):
    api.executor.side_effect=PaperExecutionError(message)
    r=post(api)
    assert r.status_code==status and r.json()['detail']['code']==code
    assert api.state['mutations']==0


def test_api_contains_no_pricing_risk_or_approval_construction():
    import ast
    from pathlib import Path
    path=Path(__file__).parents[1]/'app/execution/api'
    for file in path.glob('*.py'):
        tree=ast.parse(file.read_text())
        assert not any(isinstance(n,ast.BinOp) and isinstance(n.op,
            (ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod,ast.Pow)) for n in ast.walk(tree))
        names={n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        assert not names & {'RiskDecision','ExecutionAuthorization','ExecutionRequest','PaperCommand',
            'inspect_risk','evaluate_risk','price_fill','calculate_paper_fill'}
