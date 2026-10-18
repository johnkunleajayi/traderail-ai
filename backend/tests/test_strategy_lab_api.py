"""Authenticated stored-only experiment adapter, tested with actual research engines."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from app.main import create_app
from app.core.config import Settings
from app.db.session import get_session
from app.auth.dependencies import get_current_user
from app.market_data.binance import BinanceHistoricalProvider
from app.market_data.quidax import QuidaxPublicProvider
from app.market_data import historical_service, candle_store
from app.strategy_lab.api.router import experiment_time
from app.strategy_lab.api import service
from app.strategy_lab.api.schemas import ExperimentRequest
from app.strategy_lab.metrics import drawdown_series
from test_integrated_9c_audit import frozen_dataset, pipeline, AS_OF, COSTS

D=Decimal
URL='/api/strategy-lab/experiments'
ORIGIN='http://localhost:5173'


def payload(data=None):
    data=data or frozen_dataset('long_loss')
    return {'canonical_symbol':data.context.canonical_symbol,'provider':data.context.provider,'timeframe':data.context.timeframe,
        'requested_candle_count':data.candle_count,'evaluation_start':data.evaluation_start.isoformat(),
        'evaluation_end':data.evaluation_end.isoformat(),'initial_capital':'10000','quantity':'1',
        'execution':{'fee_rate':'.001','full_spread_rate':'.002','slippage_rate':'.001','delay_candles':0}}


@pytest.fixture
def api(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('External data/ingestion is forbidden')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',forbidden)
    monkeypatch.setattr(BinanceHistoricalProvider,'fetch_candles',forbidden)
    monkeypatch.setattr(QuidaxPublicProvider,'fetch_ticker',forbidden)
    monkeypatch.setattr(historical_service,'ingest_historical_candles',forbidden)
    monkeypatch.setattr(candle_store,'persist_candles',forbidden)
    original=frozen_dataset('long_loss')
    source=list(original.candles)
    db=Mock()
    instrument=SimpleNamespace(id=original.context.instrument_id,canonical_symbol=original.context.canonical_symbol)
    db.scalar.return_value=instrument
    def stored(query):
        assert query.is_select and query.get_execution_options()['autoflush'] is False
        parameters=query.compile().params
        end=parameters['open_time_1']; limit=query._limit_clause.value
        rows=[row for row in source if row.open_time<end]
        return SimpleNamespace(all=lambda:list(reversed(rows[-limit:])))
    db.scalars.side_effect=stored
    app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
    app.dependency_overrides[get_session]=lambda:db
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=original.context.instrument_id,status='active')
    app.dependency_overrides[experiment_time]=lambda:AS_OF
    with TestClient(app) as client:
        yield client,db,source,app,instrument
    for name in ('add','add_all','execute','commit','flush','delete','merge','bulk_save_objects'):
        getattr(db,name).assert_not_called()


def post(api,body=None):
    return api[0].post(URL,json=body or payload(),headers={'Origin':ORIGIN})


def test_authentication_and_origin_required_before_research(api):
    client,db,_,app,_=api
    del app.dependency_overrides[get_current_user]
    response=post(api); assert response.status_code==401
    assert response.headers['cache-control']=='no-store'
    db.scalar.assert_not_called();db.scalars.assert_not_called()
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(status='active')
    for origin in (None,'https://untrusted.example','null'):
        response=client.post(URL,json=payload(),headers={} if origin is None else {'Origin':origin})
        assert response.status_code==403
    db.scalar.assert_not_called();db.scalars.assert_not_called()


def test_existing_cookie_session_authentication_is_read_only(api):
    client,db,_,app,instrument=api
    del app.dependency_overrides[get_current_user]
    session=SimpleNamespace(user_id=instrument.id,expires_at=datetime.now(timezone.utc)+timedelta(days=1))
    def scalar(query):
        return session if 'auth_sessions' in str(query) else instrument
    db.scalar.side_effect=scalar
    db.get.return_value=SimpleNamespace(id=instrument.id,status='active')
    client.cookies.set('traderail_session','test-opaque-session-token')
    assert post(api).status_code==200
    db.get.return_value.status='disabled'
    assert post(api).status_code==401


def test_real_domain_pipeline_is_source_of_truth_and_exact_decimal_serialization(api):
    before=tuple(api[2]); response=post(api)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    body=response.json(); expected=pipeline('long_loss',costs=COSTS)
    assert body['experiment']['research_result_identity']==expected.result_identity
    assert body['experiment']['dataset_identity']==expected.dataset_fingerprint
    assert body['experiment']['warmup_candle_count']==128 and body['experiment']['required_warmup_candles']==52
    assert body['summary']['initial_capital']=='10000' and body['summary']['completed_trade_count']==1
    aliases={'max_drawdown':'maximum_drawdown','completed_trade_count':'trade_count','exposure':'exposure_percentage',
        'fee_total':'total_fees','spread_cost_total':'total_spread_cost','slippage_cost_total':'total_slippage_cost'}
    for field,value in body['summary'].items():
        if field in ('initial_capital','ending_equity'):continue
        domain=getattr(expected.performance,aliases.get(field,field))
        assert value==(format(domain,'f') if isinstance(domain,D) else domain)
    trade=body['trades'][0]; domain=expected.completed_trades[0]
    assert trade['entry_reference_price']=='177' and trade['exit_reference_price']=='174'
    assert trade['entry_fill_price']==format(domain.entry_fill_price,'f') and trade['exit_fill_price']==format(domain.exit_fill_price,'f')
    assert D(trade['entry_fill_price'])==D('177.354') and D(trade['exit_fill_price'])==D('173.652')
    assert trade['net_pnl']==format(domain.net_pnl,'f') and trade['fees']==format(domain.costs.fees,'f')
    assert D(trade['net_pnl'])==D('-4.053006') and D(trade['fees'])==D('.351006')
    assert trade['entry_reason_codes']==list(domain.entry_fill.decision.reason_codes)
    assert trade['exit_reason_codes']==list(domain.exit_fill.decision.reason_codes)
    assert body['summary']['ending_equity']==format(expected.equity_curve[-1].total_equity,'f')
    drawdowns=drawdown_series((expected.initial_capital,*(row.total_equity for row in expected.equity_curve)))[1:]
    assert [row['drawdown'] for row in body['equity_curve']]==[format(value,'f') if value is not None else None for value in drawdowns]
    assert tuple(api[2])==before


def test_warmup_excluded_and_regime_decision_equity_alignment(api):
    body=post(api).json(); data=frozen_dataset('long_loss')
    assert len(body['decisions'])==len(body['regimes'])==len(body['equity_curve'])==17
    assert all(row['timestamp']>=data.evaluation_start.isoformat().replace('+00:00','Z') for row in body['decisions'])
    assert [row['timestamp'] for row in body['decisions']]==[row['timestamp'] for row in body['regimes']]==[row['timestamp'] for row in body['equity_curve']]
    assert [row['open_time'] for row in body['decisions']]==[row['open_time'] for row in body['regimes']]
    assert body['trades'][0]['entry_fill_time']>body['trades'][0]['entry_decision_time']
    assert body['decisions'][0]['timestamp'].endswith('Z')


@pytest.mark.parametrize('name',['open_long','open_short','unfilled','no_trades','short_loss'])
def test_open_unfilled_no_trade_and_short_serialization(api,name):
    data=frozen_dataset(name);api[2][:]=data.candles
    response=post(api,payload(data));assert response.status_code==200,response.text
    body=response.json();expected=pipeline(name,costs=COSTS)
    assert body['experiment']['research_result_identity']==expected.result_identity
    assert len(body['trades'])==len(expected.completed_trades)
    if name.startswith('open_'):
        assert body['open_position']['direction']==expected.open_position.direction.value
        assert body['open_position']['entry_price']==format(expected.open_position.entry_price,'f')
        assert body['open_position']['unrealized_pnl']==format(expected.open_position.unrealized_pnl,'f')
        assert not body['trades']
    if name=='unfilled':
        assert body['open_position'] is None and not body['trades']
        assert body['unfilled_decisions'][0]['reason_code']=='END_OF_DATASET'
        assert body['decisions'][-1]['decision']=='ENTER_LONG'
        assert body['unfilled_decisions'][0]['decision_identity']==body['decisions'][-1]['decision_identity']
    if name=='no_trades':
        assert body['summary']['win_rate'] is body['summary']['profit_factor'] is body['summary']['expectancy'] is None


def test_deterministic_across_request_time_and_meaningful_configuration_changes(api):
    first=post(api).json();api[3].dependency_overrides[experiment_time]=lambda:AS_OF+timedelta(days=1)
    assert post(api).json()==first
    changed=payload();changed['execution']['fee_rate']='.002'
    assert post(api,changed).json()['experiment']['experiment_identity']!=first['experiment']['experiment_identity']
    equivalent=payload();equivalent['quantity']='1.0000'
    assert post(api,equivalent).json()['experiment']['experiment_identity']==first['experiment']['experiment_identity']


@pytest.mark.parametrize('field,value',[
    ('initial_capital','0'),('initial_capital','-1'),('initial_capital',10000),('initial_capital','NaN'),
    ('quantity','0'),('quantity','-1'),('quantity',1.5),('quantity','1e1000'),('quantity','0.'+'1'*25),
    ('provider','QUIDAX'),('timeframe','5m'),('requested_candle_count',52),('requested_candle_count',301),
    ('requested_candle_count',True),('requested_candle_count','100'),('evaluation_start','2025-01-01T00:00:00'),
    ('evaluation_end',12345),('evaluation_start','1735689600'),('evaluation_end','1736049600'),
    ('evaluation_start','2025-01-06T08:00:00.0000001Z'),('strategy_family','OTHER'),('strategy_version','2.0.0'),('strategy_configuration',{})])
def test_strict_malformed_requests_rejected_before_queries(api,field,value):
    body=payload();body[field]=value
    assert post(api,body).status_code==422
    api[1].scalar.assert_not_called();api[1].scalars.assert_not_called()


@pytest.mark.parametrize('field,value',[('fee_rate','-1'),('fee_rate','1.01'),('fee_rate',.01),
    ('full_spread_rate','NaN'),('slippage_rate','-1'),('delay_candles',-1),('delay_candles',1.5),
    ('delay_candles',True),('delay_candles',301),('venue','QUIDAX')])
def test_invalid_execution_assumptions_rejected(api,field,value):
    body=payload();body['execution'][field]=value
    assert post(api,body).status_code==422
    api[1].scalar.assert_not_called();api[1].scalars.assert_not_called()


def test_unknown_and_insufficient_history_are_controlled_and_never_ingested(api):
    api[1].scalar.return_value=None
    response=post(api);assert response.status_code==404 and response.json()['detail']['code']=='instrument_not_found'
    api[1].scalar.return_value=api[4];api[2].pop()
    response=post(api);assert response.status_code==409 and response.json()['detail']['code']=='insufficient_stored_history'


def test_explicit_window_no_hidden_history_and_warmup(api):
    body=payload();body['requested_candle_count']=100
    response=post(api,body);assert response.status_code==200,response.text
    assert response.json()['experiment']['candle_count']==100 and response.json()['experiment']['warmup_candle_count']==83
    query=api[1].scalars.call_args.args[0]
    assert query._limit_clause.value==100 and query.get_execution_options()['autoflush'] is False
    assert 'open_time <' in str(query) and 'DESC' in str(query)
    assert api[1].scalar.call_args.args[0].get_execution_options()['autoflush'] is False
    body['requested_candle_count']=60
    assert post(api,body).json()['detail']['code']=='insufficient_warmup'
    body=payload();body['evaluation_start']=frozen_dataset('long_loss').candles[51].open_time.isoformat()
    assert post(api,body).json()['detail']['code']=='insufficient_warmup'
    body['evaluation_start']=frozen_dataset('long_loss').candles[52].open_time.isoformat()
    assert post(api,body).status_code==200


def test_invalid_boundaries_and_incomplete_history_are_controlled(api):
    body=payload();body['evaluation_start']=body['evaluation_end']
    assert post(api,body).status_code==422
    body=payload();body['evaluation_end']=(frozen_dataset('long_loss').evaluation_end-timedelta(minutes=1)).isoformat()
    assert post(api,body).json()['detail']['code']=='invalid_evaluation_boundary'
    body=payload();body['evaluation_start']=(frozen_dataset('long_loss').evaluation_start+timedelta(minutes=1)).isoformat()
    assert post(api,body).status_code==422
    api[3].dependency_overrides[experiment_time]=lambda:frozen_dataset('long_loss').evaluation_start
    assert post(api).json()['detail']['code']=='invalid_evaluation_boundary'


def test_gap_malformed_and_future_retrieval_fail_without_repair(api):
    original=tuple(api[2]);api[2][60]=replace(api[2][60],open_time=api[2][59].open_time)
    assert post(api).json()['detail']['code']=='invalid_candle_dataset'
    api[2][:]=original;api[2][60]=replace(api[2][60],high=D('.1'))
    assert post(api).json()['detail']['code']=='invalid_candle_dataset'
    api[2][:]=original;api[2][60]=replace(api[2][60],retrieved_at=AS_OF+timedelta(days=1))
    assert post(api).json()['detail']['code']=='research_execution_failed'


def test_accounting_failure_and_sql_error_are_sanitized(api,monkeypatch):
    body=payload();body['initial_capital']='1'
    response=post(api,body);assert response.status_code==409 and response.json()['detail']['code']=='research_execution_failed'
    def failure(*args,**kwargs):raise ValueError('DO_NOT_EXPOSE_PRIVATE_CONNECTION_DETAILS')
    monkeypatch.setattr(service,'account_simulation',failure)
    assert 'DO_NOT_EXPOSE' not in post(api).text
    api[1].scalar.side_effect=SQLAlchemyError('DO_NOT_EXPOSE_PRIVATE_CONNECTION_DETAILS')
    response=post(api);assert response.status_code==503 and 'DO_NOT_EXPOSE' not in response.text


def test_non_utc_request_offsets_normalized_and_response_contains_no_floats(api):
    body=payload();data=frozen_dataset('long_loss')
    from datetime import timezone
    offset=timezone(timedelta(hours=1))
    body['evaluation_start']=data.evaluation_start.astimezone(offset).isoformat()
    body['evaluation_end']=data.evaluation_end.astimezone(offset).isoformat()
    response=post(api,body);assert response.status_code==200
    def visit(value):
        assert not isinstance(value,float)
        if isinstance(value,dict):
            for item in value.values():visit(item)
        if isinstance(value,list):
            for item in value:visit(item)
    visit(response.json())
    assert response.json()==post(api).json()


@pytest.mark.parametrize('symbol',['BTC/USDT','ETH/USDT'])
@pytest.mark.parametrize('timeframe',['15m','1h','4h','1d'])
def test_canonical_context_and_all_supported_timeframes(api,symbol,timeframe):
    from app.market_intelligence.types import DatasetContext,TIMEFRAME_SECONDS
    from app.strategy_lab.dataset import create_dataset
    from test_out_of_sample_validation import dataset as flat_dataset
    original=flat_dataset(count=60);start=original.warmup_start
    period=timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    rows=tuple(replace(row,open_time=start+index*period,close_time=start+(index+1)*period-timedelta(milliseconds=1),
        retrieved_at=start+(index+1)*period+timedelta(days=1),timeframe=timeframe) for index,row in enumerate(original.candles))
    cutoff=AS_OF+timedelta(days=365)
    data=create_dataset(DatasetContext(original.context.instrument_id,symbol,'BINANCE',timeframe),rows,
        warmup_start=start,evaluation_start=start+52*period,evaluation_end=start+60*period,as_of=cutoff)
    api[2][:]=data.candles;api[4].canonical_symbol=symbol
    api[3].dependency_overrides[experiment_time]=lambda:cutoff
    response=post(api,payload(data));assert response.status_code==200,response.text
    metadata=response.json()['experiment']
    assert (metadata['canonical_symbol'],metadata['provider'],metadata['timeframe'])==(symbol,'BINANCE',timeframe)
    assert metadata['warmup_candle_count']==52 and metadata['evaluation_candle_count']==8
    assert len(response.json()['decisions'])==8


def test_delay_stays_causal_and_final_decision_does_not_create_position(api):
    data=frozen_dataset('open_long');api[2][:]=data.candles
    body=payload(data);body['execution']['delay_candles']=1
    response=post(api,body);assert response.status_code==200,response.text
    output=response.json()
    assert output['open_position'] is None and not output['trades']
    assert output['unfilled_decisions'][0]['filled_state']=='CASH'
    assert output['experiment']['execution']['delay_candles']==1


def test_only_one_bounded_history_query_and_no_implicit_instrument_substitution(api):
    body=payload();body['canonical_symbol']='BTC/NGN'
    api[4].canonical_symbol='BTC/NGN';api[2].clear()
    response=post(api,body)
    assert response.status_code==409 and response.json()['detail']['code']=='insufficient_stored_history'
    assert api[1].scalar.call_count==api[1].scalars.call_count==1
    query=api[1].scalar.call_args.args[0]
    assert query.compile().params['canonical_symbol_1']=='BTC/NGN'


def test_openapi_declares_financial_strings_and_only_the_single_research_endpoint(api):
    document=api[0].get('/openapi.json').json()
    request=document['components']['schemas']['ExperimentRequest']['properties']
    assert request['initial_capital']['type']==request['quantity']['type']=='string'
    assert document['components']['schemas']['ExecutionRequest']['properties']['fee_rate']['type']=='string'
    assert document['components']['schemas']['SummaryResponse']['properties']['ending_equity']['type']=='string'
    assert [path for path in document['paths'] if path.startswith('/api/strategy-lab/')]==[URL,'/api/strategy-lab/validations']


def test_invalid_input_errors_are_not_cacheable(api):
    body=payload();body['quantity']=0
    response=post(api,body)
    assert response.status_code==422 and response.headers['cache-control']=='no-store'
    assert response.headers['referrer-policy']=='no-referrer'


def test_capital_quantity_and_boundaries_are_bound_to_experiment_identity(api):
    original=post(api).json()['experiment']['experiment_identity']
    body=payload();body['initial_capital']='20000'
    assert post(api,body).json()['experiment']['experiment_identity']!=original
    body=payload();body['quantity']='2'
    assert post(api,body).json()['experiment']['experiment_identity']!=original
    body=payload();body['evaluation_start']=frozen_dataset('long_loss').candles[129].open_time.isoformat()
    assert post(api,body).json()['experiment']['experiment_identity']!=original
