"""Real unchanged analysis/strategy engines over deterministic stored-candle fixtures.
Only SQL boundaries are doubled. No PostgreSQL/provider/execution activity.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4
import ast
import json
import os
import subprocess
import sys
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from app.auth.dependencies import get_current_user
from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.instruments.models import Instrument
from app.portfolio.models import PaperPortfolio
from app.mandate_eligibility.policy import POLICY_V1
from app.mandate_eligibility.service import resolution_time
from app.mandate_strategy import service, pipeline
from app.mandate_strategy.contracts import MandateEvaluationError, ObservationOutcome
from app.risk.paper_source import market as canonical_market
from app.strategies.decisions import DecisionType
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from test_integrated_9c_audit import frozen_dataset

D=Decimal
OWNER=UUID(int=2);MID=UUID(int=4);PID=UUID(int=5)
ORIGIN='http://localhost:5173'


def rows_for(kind='no_trades'):
    dataset=frozen_dataset(kind)
    rows=dataset.candles[:131] if kind!='no_trades' else dataset.candles
    rows=tuple(replace(row,retrieved_at=row.close_time+timedelta(milliseconds=1)) for row in rows)
    at=rows[-1].open_time+timedelta(hours=1,minutes=1)
    return dataset.context,rows,at


class Result:
    def __init__(self,rows):self.rows=tuple(rows)
    def __iter__(self):return iter(self.rows)
    def mappings(self):return self
    def one_or_none(self):return self.rows[0] if self.rows else None
    def all(self):return list(self.rows)
    def scalars(self):return iter(self.rows)


class StoredConnection:
    def __init__(self,context,rows,at):
        self.context=context;self.rows=rows;self.at=at;self.statements=[];self.open_positions=()
        self.portfolio=dict(id=PID,user_id=OWNER,environment='PAPER',base_currency='USDT',cash_balance=D('10000'),status='active',updated_at=datetime(2025,1,1,tzinfo=timezone.utc))
        self.instrument=dict(id=context.instrument_id,canonical_symbol='BTC/USDT',market_type='SPOT',status='active',base_asset_id=UUID(int=6),quote_asset_id=UUID(int=7))
    def execute(self,statement):
        self.statements.append(statement)
        table=statement.get_final_froms()[0].name
        if table=='paper_portfolios':return Result([self.portfolio])
        if table=='paper_positions':return Result(self.open_positions)
        if table=='paper_accounting_entries':return Result(())
        if table=='instruments':return Result([self.instrument])
        if table=='assets':return Result([(UUID(int=6),'BTC'),(UUID(int=7),'USDT')])
        if table=='market_candles':
            params=statement.compile().params
            assert params['instrument_id_1']==self.context.instrument_id
            assert params['provider_1']=='BINANCE' and params['timeframe_1']=='1h'
            selected=sorted((row for row in self.rows if row.close_time<params['close_time_1']),key=lambda row:row.open_time,reverse=True)[:params['param_1']]
            return Result(asdict(row) for row in selected)
        raise AssertionError('Unexpected table: '+table)


@pytest.fixture
def make_api(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Risk/TradeIntent/authorization/execution/ingestion forbidden')
    from app.risk.integrated import evaluator
    from app.risk import authorization_service, adaptive_trend_intent
    from app.execution.paper import service as paper
    from app.market_data import historical_service,candle_store
    for obj,name in [(evaluator,'evaluate_risk'),(authorization_service,'evaluate_and_register'),(adaptive_trend_intent,'create_adaptive_trend_intent'),(paper,'execute_paper'),(historical_service,'ingest_historical_candles'),(candle_store,'persist_candles')]:monkeypatch.setattr(obj,name,forbidden)
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',forbidden)
    clients=[]
    def make(kind='no_trades'):
        context,rows,at=rows_for(kind);connection=StoredConnection(context,rows,at)
        intent=SimpleNamespace(id=MID,user_id=OWNER,environment='PAPER',allocation_currency='USDT',capital_allocation=D('500.00'),instrument='BTC/USDT',risk_level='BALANCED',trading_style='MEDIUM_TERM',direction_capability='LONG_ONLY',status='ACTIVE',created_at=datetime(2025,1,1,tzinfo=timezone.utc),updated_at=datetime(2025,1,1,tzinfo=timezone.utc))
        user=SimpleNamespace(id=OWNER,status='active');db=Mock();queries=[]
        def scalar(query):
            queries.append(query);params=query.compile().params
            entity=query.column_descriptions[0]['entity']
            if entity is Instrument:return SimpleNamespace(**connection.instrument)
            if entity is PaperPortfolio:return PID if params['user_id_1']==OWNER else None
            return intent if params.get('id_1')==MID and params.get('user_id_1')==OWNER else None
        db.scalar.side_effect=scalar;db.connection.return_value=connection
        app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
        app.dependency_overrides[get_current_user]=lambda:user
        app.dependency_overrides[get_session]=lambda:db
        app.dependency_overrides[resolution_time]=lambda:connection.at
        client=TestClient(app);clients.append(client)
        return SimpleNamespace(client=client,app=app,user=user,db=db,connection=connection,intent=intent,queries=queries,context=context)
    yield make
    for client in clients:client.close()


def post(api,body=None,origin=ORIGIN):
    return api.client.post(f'/api/mandates/{MID}/evaluate',json={} if body is None else body,headers={} if origin is None else {'Origin':origin})


@pytest.mark.parametrize('kind,decision,outcome',[('no_trades','NO_ACTION','NO_TRADE'),('long_profit','ENTER_LONG','ENTER_LONG'),('short_profit','ENTER_SHORT','NO_TRADE')])
def test_real_canonical_engines_and_genuine_decisions(make_api,kind,decision,outcome):
    api=make_api(kind)
    response=post(api);assert response.status_code==200,response.text
    data=response.json()
    assert data['strategy_decision']['decision']==decision
    assert data['outcome']==outcome
    assert data['strategy_decision']['configuration_identity']==AdaptiveTrendStrategy().definition.configuration_identity
    assert data['scope']=='STRATEGY_DECISION_ONLY' and data['status']=='EVALUATED'
    assert data['eligibility_policy_version']=='mandate-eligibility-v2'
    assert data['market']['provider']=='BINANCE' and data['market']['timeframe']=='1h' and data['market']['history_count']==100
    assert data['market']['candle_close_time']==data['strategy_decision']['timestamp']
    if decision=='ENTER_SHORT':assert data['reason_codes']==['UNSUPPORTED_DIRECTION']
    if decision=='NO_ACTION':assert data['reason_codes']==['STRATEGY_NO_ACTION']
    assert data['user_intent']['capital_allocation']=='500.00'
    assert response.headers['cache-control']=='no-store'
    for name in ('add','delete','flush','commit'):getattr(api.db,name).assert_not_called()
    for statement in api.connection.statements:assert str(statement).lstrip().startswith('SELECT')


@pytest.mark.parametrize('field,value',[('status','DRAFT'),('status','PAUSED'),('status','RETIRED'),('risk_level','GROWTH'),('risk_level','CONSERVATIVE'),('trading_style','SHORT_TERM'),('trading_style','LONGER_TERM'),('environment','LIVE'),('instrument','ETH/USDT'),('allocation_currency','USD'),('direction_capability','SHORT')])
def test_ineligible_never_reaches_book_or_market_or_strategy(make_api,monkeypatch,field,value):
    api=make_api();setattr(api.intent,field,value)
    calls=Mock(side_effect=AssertionError('Must stop before market and strategy'))
    monkeypatch.setattr(pipeline,'read_observation',calls)
    response=post(api)
    assert response.status_code==409 and response.json()['detail']['code']=='mandate_ineligible'
    api.db.connection.assert_not_called();calls.assert_not_called()
    assert api.connection.statements==[]


def test_historical_v1_stops_before_evidence(make_api,monkeypatch):
    from app.mandate_eligibility import service as eligibility_service
    monkeypatch.setattr(eligibility_service,'POLICY',POLICY_V1)
    api=make_api();assert post(api).status_code==409
    assert api.connection.statements==[]


@pytest.mark.parametrize('change',['missing','insufficient','stale','future_retrieval','gap','malformed','wrong_provider','wrong_timeframe'])
def test_bad_market_evidence_fails_closed(make_api,change):
    api=make_api();c=api.connection
    if change=='missing':c.rows=()
    elif change=='insufficient':c.rows=c.rows[-99:]
    elif change=='stale':c.at+=timedelta(minutes=10)
    elif change=='future_retrieval':c.rows=(*c.rows[:-1],replace(c.rows[-1],retrieved_at=c.at+timedelta(seconds=1)))
    elif change=='gap':c.rows=tuple(row for i,row in enumerate(c.rows) if i!=len(c.rows)-50)
    elif change=='malformed':c.rows=(*c.rows[:-1],replace(c.rows[-1],close=D('-1')))
    elif change=='wrong_provider':c.rows=tuple(replace(row,provider='OTHER') for row in c.rows)
    else:c.rows=tuple(replace(row,timeframe='4h') for row in c.rows)
    response=post(api);assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='market_evidence_unavailable'
    for name in ('add','flush','commit'):getattr(api.db,name).assert_not_called()


def test_incomplete_candle_is_excluded_by_canonical_stored_query(make_api):
    api=make_api();first=post(api).json()
    last=api.connection.rows[-1]
    incomplete=replace(last,open_time=last.open_time+timedelta(hours=1),close_time=last.close_time+timedelta(hours=1),retrieved_at=last.retrieved_at+timedelta(hours=1))
    api.connection.rows=(*api.connection.rows,incomplete)
    response=post(api);assert response.status_code==200
    assert response.json()['evaluation_identity']==first['evaluation_identity']
    sql=[str(query.compile(dialect=postgresql.dialect())) for query in api.connection.statements]
    assert any('market_candles.close_time <' in query and 'ORDER BY market_candles.open_time DESC' in query for query in sql)


@pytest.mark.parametrize('change',['held_position','missing_portfolio','wrong_owner','wrong_currency','wrong_environment','disabled'])
def test_unknown_or_non_cash_book_never_assumed_flat(make_api,monkeypatch,change):
    api=make_api();c=api.connection
    if change=='held_position':c.open_positions=({'quantity':D('1'),'instrument':'BTC/USDT'},)
    elif change=='missing_portfolio':api.db.scalar.side_effect=lambda query:None if query.column_descriptions[0]['entity'] is PaperPortfolio else SimpleNamespace(**c.instrument) if query.column_descriptions[0]['entity'] is Instrument else api.intent
    elif change=='wrong_owner':c.portfolio['user_id']=uuid4()
    elif change=='wrong_currency':c.portfolio['base_currency']='USD'
    elif change=='wrong_environment':c.portfolio['environment']='LIVE'
    else:c.portfolio['status']='disabled'
    blocked=Mock(side_effect=AssertionError('No analytical evaluation without proven context'))
    monkeypatch.setattr(pipeline,'read_observation',blocked)
    response=post(api);assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='position_context_unavailable';blocked.assert_not_called()


@pytest.mark.parametrize('body',[{'quantity':'1'},{'price':'100'},{'capital_allocation':'10000'},{'position_state':'LONG'},{'as_of':'2026-10-25T00:00:00Z'},{'strategy':'override'}])
def test_http_cannot_supply_financial_or_analytical_truth(make_api,body):
    api=make_api();assert post(api,body).status_code==422
    api.db.connection.assert_not_called()


@pytest.mark.parametrize('origin',[None,'https://evil.example',ORIGIN+'/'])
def test_origin_protection(make_api,origin):
    api=make_api();assert post(api,origin=origin).status_code==403
    api.db.connection.assert_not_called()


def test_anonymous_requires_authentication():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.post(f'/api/mandates/{MID}/evaluate',json={},headers={'Origin':ORIGIN}).status_code==401


def test_other_owner_and_missing_mandate_are_not_found(make_api):
    api=make_api();api.user.id=uuid4()
    assert post(api).status_code==404
    api.db.connection.assert_not_called()
    assert api.client.post(f'/api/mandates/{uuid4()}/evaluate',json={},headers={'Origin':ORIGIN}).status_code==404


def test_database_failure_is_sanitized(make_api):
    api=make_api();api.db.scalar.side_effect=OperationalError('private query',{},Exception('secret credential'))
    response=post(api)
    assert response.status_code==503 and 'private' not in response.text and 'secret' not in response.text


def test_no_threshold_changes_and_no_quantity_in_observation(make_api):
    api=make_api('long_profit')
    first=post(api).json();api.intent.capital_allocation=D('500.12345678901234567890123456789')
    second=post(api).json()
    assert first['strategy_decision']==second['strategy_decision']
    assert first['evaluation_identity']!=second['evaluation_identity']
    assert second['user_intent']['capital_allocation']=='500.12345678901234567890123456789'
    assert 'quantity' not in json.dumps(second) and 'position_size' not in json.dumps(second)
    assert second['technical_configuration']['timeframe_role']=='SETUP_EVALUATION'


def test_output_determinism_and_provenance(make_api):
    api=make_api('long_profit')
    first=post(api).json();second=post(api).json();assert first==second
    api.connection.at+=timedelta(seconds=1)
    later=post(api).json()
    assert first['strategy_decision']==later['strategy_decision'] and first['evaluation_identity']==later['evaluation_identity']
    for field in ('market_evidence_identity','intelligence_identity','regime_identity'):assert first['market'][field].startswith('sha256:')
    assert first['strategy_decision']['input_identity'].startswith('sha256:')
    assert first['regime']['regime']=='BULL_TREND'
    assert first['strategy_decision']['timestamp']==first['market']['candle_close_time']


def test_each_existing_engine_called_once(make_api,monkeypatch):
    api=make_api();features=Mock(wraps=canonical_market.calculate_features);regimes=Mock(wraps=canonical_market.classify_regimes)
    monkeypatch.setattr(canonical_market,'calculate_features',features);monkeypatch.setattr(canonical_market,'classify_regimes',regimes)
    original=AdaptiveTrendStrategy.decide;calls=[]
    def decide(self,observation):calls.append(observation);return original(self,observation)
    monkeypatch.setattr(AdaptiveTrendStrategy,'decide',decide)
    assert post(api).status_code==200
    assert features.call_count==regimes.call_count==len(calls)==1
    assert calls[0].position_state.value=='CASH'


def test_result_is_immutable(make_api):
    api=make_api()
    result=service.evaluate_mandate(api.db,OWNER,MID,evaluated_at=api.connection.at)
    with pytest.raises(ValidationError):result.outcome=ObservationOutcome.ENTER_LONG
    with pytest.raises(ValidationError):result.market.timeframe='4h'
    with pytest.raises(ValidationError):result.strategy_decision.reason_codes=('override',)


def test_direction_filter_preserves_short_and_no_action_semantics():
    assert pipeline.filter_capability(DecisionType.ENTER_SHORT)[0] is ObservationOutcome.NO_TRADE
    assert pipeline.filter_capability(DecisionType.EXIT_SHORT)[1][0].value=='UNSUPPORTED_DIRECTION'
    assert pipeline.filter_capability(DecisionType.NO_ACTION)[0] is ObservationOutcome.NO_TRADE
    assert pipeline.filter_capability(DecisionType.EXIT_LONG)[0] is ObservationOutcome.NO_TRADE


def test_no_authorization_or_economic_service_imports():
    for path in (Path(__file__).parents[1]/'app/mandate_strategy').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.ImportFrom):assert not (node.module or '').startswith(('app.risk.integrated','app.risk.authorization_service','app.risk.adaptive_trend_intent','app.execution.paper.service','app.execution.registry'))


def test_four_fresh_hash_seeds_preserve_real_strategy_result():
    script = """
import json, sys
sys.path.insert(0, 'tests')
import pytest
import test_mandate_strategy as t
patch = pytest.MonkeyPatch()
generator = t.make_api.__wrapped__(patch)
try:
    make = next(generator)
    outputs = []
    for kind in ('long_profit', 'short_profit', 'no_trades'):
        api = make(kind)
        result = t.service.evaluate_mandate(api.db, t.OWNER, t.MID, evaluated_at=api.connection.at)
        outputs.append(result.model_dump(mode='json'))
    print(json.dumps(outputs, sort_keys=True))
finally:
    generator.close()
    patch.undo()
"""
    results=[]
    for seed in ('1','7','42','12345'):
        completed=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).parents[1],
            env={**os.environ,'PYTHONHASHSEED':seed},capture_output=True,text=True,check=False,timeout=60)
        assert completed.returncode==0,completed.stderr
        results.append(completed.stdout)
    assert len(set(results))==1
