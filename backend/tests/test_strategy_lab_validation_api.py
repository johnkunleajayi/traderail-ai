"""Stored-only validation orchestration with unchanged research engines and guarded mocks."""
from dataclasses import replace,asdict
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import copy
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
from app.market_data import historical_service,candle_store
from app.strategy_lab.api.router import experiment_time
from app.strategy_lab.api import validation_service as service
from app.strategy_lab.api.validation_request import ValidationRequest
from app.strategy_lab.api.validation_presentation import validation_response
from app.strategy_lab.api.validation_config import SENSITIVITY_CONFIGURATIONS,stress_scenarios
from app.strategy_lab.validation_evidence import DEFAULT_RESEARCH_POLICY,Verdict
from app.strategy_lab.costs import ResearchCostModel
from test_out_of_sample_validation import dataset,AS_OF,D
from test_integrated_9c_audit import frozen_dataset

URL='/api/strategy-lab/validations'
ORIGIN='http://localhost:5173'


def boundaries(data):
    start=data.evaluation_start
    return {'warmup_start':data.warmup_start.isoformat(),'in_sample_start':start.isoformat(),
        'out_of_sample_start':(start+timedelta(hours=2)).isoformat(),'end':data.evaluation_end.isoformat()}


def payload(data=None):
    data=data or dataset(count=60)
    window=boundaries(data)
    return {'canonical_symbol':data.context.canonical_symbol,'provider':'BINANCE','timeframe':'1h',
        'requested_candle_count':data.candle_count,'primary':window,'walk_forward_folds':[dict(window)],
        'initial_capital':'10000','quantity':'1','execution':{'fee_rate':'0.001','full_spread_rate':'0.002','slippage_rate':'0.001','delay_candles':0}}


@pytest.fixture
def api(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('No provider/network/ingestion permitted')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',forbidden)
    monkeypatch.setattr(BinanceHistoricalProvider,'fetch_candles',forbidden)
    monkeypatch.setattr(QuidaxPublicProvider,'fetch_ticker',forbidden)
    monkeypatch.setattr(historical_service,'ingest_historical_candles',forbidden)
    monkeypatch.setattr(candle_store,'persist_candles',forbidden)
    data=dataset(count=60);source=list(data.candles);db=Mock()
    instrument=SimpleNamespace(id=data.context.instrument_id,canonical_symbol=data.context.canonical_symbol)
    db.scalar.return_value=instrument
    def selected(query):
        assert query.is_select and query.get_execution_options()['autoflush'] is False
        args=query.compile().params
        rows=[row for row in source if row.instrument_id==args['instrument_id_1'] and row.provider==args['provider_1']
            and row.timeframe==args['timeframe_1'] and args['open_time_1']<=row.open_time<args['open_time_2']]
        return SimpleNamespace(all=lambda:rows[:query._limit_clause.value])
    db.scalars.side_effect=selected
    app=create_app(Settings(_env_file=None,frontend_url=ORIGIN))
    app.dependency_overrides[get_session]=lambda:db
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(status='active',id=instrument.id)
    app.dependency_overrides[experiment_time]=lambda:AS_OF
    with TestClient(app) as client:
        yield client,db,source,app,instrument
    for name in ('add','add_all','execute','commit','flush','delete','merge','bulk_save_objects'):
        getattr(db,name).assert_not_called()


def post(api,body=None):return api[0].post(URL,json=body or payload(),headers={'Origin':ORIGIN})


def test_auth_and_origin_before_any_queries(api):
    del api[3].dependency_overrides[get_current_user]
    response=post(api);assert response.status_code==401 and response.headers['cache-control']=='no-store'
    api[1].scalar.assert_not_called();api[1].scalars.assert_not_called()
    api[3].dependency_overrides[get_current_user]=lambda:SimpleNamespace(status='active')
    for origin in (None,'https://untrusted.example','null'):
        assert api[0].post(URL,json=payload(),headers={} if origin is None else {'Origin':origin}).status_code==403
    api[1].scalar.assert_not_called()


def test_real_pipeline_complete_evidence_default_policy_and_no_mutation(api):
    before=tuple(api[2]);response=post(api)
    assert response.status_code==200,response.text
    body=response.json()
    assert body['validation']['verdict']=='INSUFFICIENT_EVIDENCE'
    assert body['validation']['policy_identity']==DEFAULT_RESEARCH_POLICY.policy_identity
    assert body['validation']['policy_configuration']['minimum_oos_completed_trades']==30
    assert body['validation']['policy_configuration']['maximum_oos_drawdown']=='0.20'
    assert body['validation']['candle_count']==60
    assert body['primary_oos']['metrics']['trade_count']==0
    assert body['primary_oos']['metrics']['expectancy'] is None
    assert body['walk_forward']['summary']['fold_count']==1 and body['walk_forward']['active_folds']==0
    assert len(body['sensitivity'])==2 and len(body['stress'])==6 and len(body['benchmarks'])==2
    assert all(len(row['changed_fields'])==1 for row in body['sensitivity'])
    assert [row['code'] for row in body['stress']]==sorted(row['code'] for row in body['stress'])
    assert [row['configuration_identity'] for row in body['sensitivity']]==sorted(row['configuration_identity'] for row in body['sensitivity'])
    assert body['validation']['initial_capital']=='10000' and body['validation']['quantity']=='1'
    assert all(row['code'] and row['reason_code'] and row['explanation'] for row in body['criteria'])
    assert body['verdict_explanation']['insufficiency_reasons']
    assert tuple(api[2])==before
    assert api[1].scalar.call_count==api[1].scalars.call_count==1
    assert response.headers['cache-control']=='no-store'
    def verify(value):
        assert not isinstance(value,float)
        if isinstance(value,dict):
            assert not {'rank','best','selected','promotion','execution_authority','lifecycle'} & value.keys()
            for item in value.values():verify(item)
        elif isinstance(value,list):
            for item in value:verify(item)
    verify(body)


def test_determinism_and_source_identities_exclude_request_time(api):
    first=post(api).json();api[3].dependency_overrides[experiment_time]=lambda:AS_OF+timedelta(days=1)
    assert post(api).json()==first
    changed=payload();changed['execution']['fee_rate']='0.002'
    assert post(api,changed).json()['validation']['validation_identity']!=first['validation']['validation_identity']


def test_benchmarks_bind_primary_oos_and_exact_context(api):
    body=post(api).json()
    for row in body['benchmarks']:
        assert row['dataset_identity']==body['validation']['dataset_identity']
        assert row['canonical_symbol']=='BTC/USDT' and row['quote_currency']=='USDT'
        assert row['initial_capital']=='10000'
        assert set(row['strategy_minus_benchmark'])=={'total_return','maximum_drawdown','exposure_percentage','turnover','total_cost'}
    cash=next(row for row in body['benchmarks'] if row['kind']=='CASH')
    assert cash['ending_equity']=='10000' and D(cash['total_return'])==0 and D(cash['fees'])==0


def test_api_fixed_probes_and_stress_do_not_mutate_defaults():
    from app.strategies.adaptive_trend.config import DEFAULT_CONFIGURATION
    baseline=asdict(DEFAULT_CONFIGURATION)
    assert SENSITIVITY_CONFIGURATIONS[0] is DEFAULT_CONFIGURATION
    assert SENSITIVITY_CONFIGURATIONS[1].entry_min_ema_spread==D('.0016')
    assert SENSITIVITY_CONFIGURATIONS[2].entry_fast_slope==D('.00021')
    costs=ResearchCostModel(D('.001'),D('.002'),D('.001'))
    scenarios={row.code:row for row in stress_scenarios(costs,timedelta(hours=1))}
    assert scenarios['HIGHER_FEE'].costs.trading_fee_rate==D('.002')
    assert scenarios['WIDER_SPREAD'].costs.spread_rate==D('.004')
    assert scenarios['HIGHER_SLIPPAGE'].costs.slippage_rate==D('.002')
    assert scenarios['ADDITIONAL_DELAY'].costs.execution_delay==timedelta(hours=1)
    assert asdict(DEFAULT_CONFIGURATION)==baseline


def test_stressed_unaffordable_execution_is_explicit_not_discarded(api):
    data=frozen_dataset('long_loss');api[2][:]=data.candles
    request=payload(data);request['initial_capital']='178'
    response=post(api,request);assert response.status_code==200,response.text
    body=response.json();failed=[row for row in body['stress'] if row['execution_failure']]
    assert failed and len(body['stress'])==6
    for row in failed:
        assert row['execution_failure']=='UNAFFORDABLE_EXECUTION'
        assert row['metrics'] is row['validation_identity'] is row['differences'] is None
        assert row['failure_source_identity'].startswith('sha256:')
    assert any(row['reason_code']=='STRESSED_EXECUTION_UNAFFORDABLE' for row in body['criteria'])
    assert body['validation']['verdict']=='INSUFFICIENT_EVIDENCE'  # Quantity coverage takes precedence.


@pytest.mark.parametrize('field,value',[
    ('provider','QUIDAX'),('timeframe','5m'),('requested_candle_count',53),('requested_candle_count',301),
    ('requested_candle_count',True),('initial_capital',10000),('initial_capital','0'),('quantity','-1'),
    ('quantity',.1),('strategy_family','OTHER'),('strategy_version','2.0.0'),('policy',{}),
    ('strategy_configuration',{}),('sensitivity_variants',[]),('stress_scenarios',[])])
def test_strict_request_disallows_tuning_and_malformed_values(api,field,value):
    body=payload();body[field]=value
    assert post(api,body).status_code==422
    api[1].scalar.assert_not_called()


@pytest.mark.parametrize('change',['reverse','overlap','unordered','outside','warmup','unaligned','count','numeric_time'])
def test_invalid_boundary_windows_fail_before_queries(api,change):
    body=payload();window=body['primary']
    if change=='reverse':window['out_of_sample_start']=window['in_sample_start']
    if change=='overlap':body['walk_forward_folds']*=2
    if change=='unordered':
        body['walk_forward_folds']=[dict(body['walk_forward_folds'][0]),dict(body['walk_forward_folds'][0])]
    if change=='outside':body['walk_forward_folds'][0]['end']=(dataset(count=60).evaluation_end+timedelta(hours=1)).isoformat()
    if change=='warmup':window['in_sample_start']=(dataset(count=60).warmup_start+timedelta(hours=51)).isoformat()
    if change=='unaligned':window['out_of_sample_start']=(dataset(count=60).evaluation_start+timedelta(hours=2,minutes=1)).isoformat()
    if change=='count':body['requested_candle_count']=61
    if change=='numeric_time':window['warmup_start']='1735689600'
    response=post(api,body);assert response.status_code==422,response.text
    api[1].scalar.assert_not_called()


def test_exact_instrument_provider_timeframe_history_not_total_count(api):
    original=tuple(api[2]);api[2][:]=[replace(row,provider='OTHER') for row in original]*10
    response=post(api);assert response.status_code==409 and response.json()['detail']['code']=='insufficient_stored_history'
    api[2][:]=[replace(row,timeframe='4h') for row in original]*10
    assert post(api).status_code==409
    api[2][:]=original;api[1].scalar.return_value=None
    assert post(api).status_code==404


def test_sanitized_database_construction_and_execution_failures(api,monkeypatch):
    api[1].scalar.side_effect=SQLAlchemyError('PRIVATE_CONNECTION_MUST_NOT_LEAK')
    response=post(api);assert response.status_code==503 and 'PRIVATE_CONNECTION' not in response.text
    api[1].scalar.side_effect=None
    body=payload();body['execution']['fee_rate']='1'
    response=post(api,body);assert response.status_code==422 and response.json()['detail']['code']=='validation_construction_failed'
    def broken(*args,**kwargs):raise ValueError('PRIVATE_CONNECTION_MUST_NOT_LEAK')
    monkeypatch.setattr(service,'validate_out_of_sample',broken)
    response=post(api);assert response.status_code==409 and 'PRIVATE_CONNECTION' not in response.text


@pytest.mark.parametrize('verdict',list(Verdict))
def test_each_domain_verdict_transports_exactly_without_api_reclassification(api,monkeypatch,verdict):
    # Serializer-only fixture: domain verdict behavior is covered by unchanged 9D tests.
    completed=service.run_stored_validation(api[1],ValidationRequest.model_validate(payload()),as_of=AS_OF)
    transport=replace(completed,report=replace(completed.report,verdict=verdict))
    monkeypatch.setattr('app.strategy_lab.api.validation_router.run_stored_validation',lambda *a,**k:transport)
    assert post(api).json()['validation']['verdict']==verdict.value


def test_domain_report_is_the_verdict_source_and_serialization_preserves_decimal_strings(api):
    request=ValidationRequest.model_validate(payload())
    completed=service.run_stored_validation(api[1],request,as_of=AS_OF)
    response=validation_response(completed).model_dump(mode='json')
    assert response['validation']['report_identity']==completed.report.report_identity
    assert response['validation']['source_evidence_identities']==dict(completed.report.source_identities)
    assert response['criteria'][0]['status']==completed.report.criteria[0].status.value
    assert response['primary_oos']['metrics']['net_return']==format(completed.evidence.oos.out_of_sample.performance.net_return,'f')


@pytest.mark.parametrize('field,value',[('fee_rate',.01),('full_spread_rate','-0.1'),('slippage_rate','1.1'),('delay_candles',-1),('delay_candles',True)])
def test_invalid_execution_assumptions_rejected_without_query(api,field,value):
    body=payload();body['execution'][field]=value
    assert post(api,body).status_code==422
    api[1].scalar.assert_not_called();api[1].scalars.assert_not_called()


def test_shared_validation_engines_are_invoked_and_stress_baseline_is_reused(api,monkeypatch):
    wrappers={}
    for name in ('validate_out_of_sample','validate_walk_forward','evaluate_sensitivity','evaluate_benchmarks','evaluate_validation_evidence'):
        wrapper=Mock(wraps=getattr(service,name));wrappers[name]=wrapper;monkeypatch.setattr(service,name,wrapper)
    runner=Mock(wraps=service.stress_engine._run)
    monkeypatch.setattr(service.stress_engine,'_run',runner)
    response=post(api);assert response.status_code==200,response.text
    assert all(wrapper.call_count==1 for wrapper in wrappers.values())
    assert runner.call_count==5
    assert all(call.args[1].code!='BASELINE' for call in runner.call_args_list)
    assert wrappers['evaluate_validation_evidence'].call_args.args[1] is DEFAULT_RESEARCH_POLICY


def test_multiple_folds_follow_explicit_disjoint_history_and_cannot_be_reordered(api):
    data=dataset(count=180);api[2][:]=data.candles
    body=payload(data)
    def stamp(index):return (data.warmup_start+timedelta(hours=index)).isoformat()
    body['walk_forward_folds']=[{'warmup_start':stamp(offset),'in_sample_start':stamp(offset+52),
        'out_of_sample_start':stamp(offset+54),'end':stamp(offset+60)} for offset in (0,60,120)]
    response=post(api,body);assert response.status_code==200,response.text
    result=response.json()
    assert result['walk_forward']['summary']['fold_count']==3
    assert len(result['walk_forward']['folds'])==3
    assert all(row['metrics']['trade_count']==0 for row in result['walk_forward']['folds'])
    body['walk_forward_folds'].reverse()
    assert post(api,body).status_code==422


def test_corrupt_stored_candles_are_not_interpolated_or_silently_skipped(api):
    before=tuple(api[2]);api[2][20]=replace(api[2][20],high=D('.01'))
    response=post(api);assert response.status_code==409 and response.json()['detail']['code']=='invalid_candle_dataset'
    assert api[2][20].high==D('.01') and len(api[2])==len(before)
