"""Offline M18.2 safeguards. Synthetic fixtures are NEVER market-performance evidence."""
from collections import Counter
from dataclasses import replace,asdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import ast,csv,hashlib,io,json,zipfile
import pytest
from app.strategy_lab.historical.parity import engine,data,report
from app.strategy_lab.historical.parity.engine import Settings,replay,allowed
from app.strategy_lab.historical.parity.data import CONTEXT,HOUR,EPOCH
from app.strategy_lab.historical.report import json_values
from app.strategy_lab.numeric import exact_sum,difference
from app.market_regimes.types import Regime
from app.strategies.inputs import PositionState
from app.strategies.decisions import DecisionType,StrategyDecision
from test_integrated_9c_audit import frozen_dataset
D=Decimal


def run(kind='long_profit',settings=None,events=None,rows=None):
    ds=frozen_dataset(kind);rows=rows or ds.candles
    return replay(ds.context,rows,start=ds.evaluation_start,end=rows[-1].open_time+HOUR,
        settings=settings or Settings(D('10000'),D('1'),D('500')),
        emit=(events.append if events is not None else lambda _:None))


def test_rolling_window_exact_canonical_parity_and_no_future_leak(monkeypatch):
    ds=frozen_dataset('long_profit');observations=[];original=engine.calculate_features
    def observed(context,window,**kwargs):
        assert len(window)==100;observations.append(tuple(window));return original(context,window,**kwargs)
    monkeypatch.setattr(engine,'calculate_features',observed)
    events=[];first=run(events=events)
    assert observations[0]==ds.candles[29:129]
    assert all(w[-1].open_time-w[0].open_time==99*HOUR for w in observations)
    changed=(*ds.candles[:-1],replace(ds.candles[-1],close=D('240'),high=D('241')))
    future=[];run(rows=changed,events=future)
    boundary=ds.candles[-1].open_time
    def before(e):return e.get('timestamp',e.get('time',''))<boundary.isoformat() if e.get('timestamp',e.get('time')) else False
    assert [e for e in events if before(e)]==[e for e in future if before(e)]
    assert first['metrics']['production_authorized_trades']==0
    assert first['metrics']['modeled_risk_blocked_opportunities'] is None


def test_real_strategy_entry_hold_exit_and_exact_costs():
    events=[];r=run('multiple',events=events)
    assert r['counts']['simulated_entries']==r['counts']['simulated_exits']==1
    assert r['counts']['hold']>0 and r['metrics']['completed_trades']==1
    t=r['trades'][0]
    assert difference(D(r['metrics']['ending_cash']),D('10000'))==D(t['net_pnl'])
    assert difference(D(t['gross_pnl']),exact_sum(D(v) for v in t['costs'].values()))==D(t['net_pnl'])
    assert D(r['metrics']['modeled_fees'])>0 and D(r['metrics']['modeled_slippage'])>0
    assert all(e['fill']['action'] in ('ENTER_LONG','EXIT_LONG') for e in events if e['kind']=='RESEARCH_FILL_NOT_AUTHORIZED')
    assert r['counts']['terminal_barrier_hours']>0


def test_short_signals_are_not_filled_and_no_trade_reasons_are_real():
    r=run('short_profit')
    assert r['metrics']['completed_trades']==0 and r['metrics']['open_positions']==0
    assert r['counts']['unsupported_direction_or_state']>0
    assert r['reason_counts'] and r['metrics']['win_rate'] is None
    empty=run('no_trades')
    assert empty['counts']['no_action']>0 and D(empty['metrics']['net_return'])==0


@pytest.mark.parametrize('regime,decision,state,expected',[
    (Regime.UNKNOWN,DecisionType.NO_ACTION,PositionState.CASH,True),
    (Regime.UNSAFE,DecisionType.NO_ACTION,PositionState.CASH,False),
    (Regime.UNKNOWN,DecisionType.ENTER_LONG,PositionState.CASH,False),
    (Regime.UNKNOWN,DecisionType.EXIT_LONG,PositionState.CASH,False),
    (Regime.UNKNOWN,DecisionType.EXIT_LONG,PositionState.LONG,True),
])
def test_exact_entry_vs_existing_position_boundaries(regime,decision,state,expected):
    assert allowed(regime,decision,state)==expected


def flat_rows(count=350):
    start=datetime(2024,1,1,tzinfo=timezone.utc)
    from app.market_intelligence.types import Candle
    return tuple(Candle(CONTEXT.instrument_id,'BINANCE','1h',start+i*HOUR,start+(i+1)*HOUR-timedelta(milliseconds=1),
        D('100'),D('101'),D('99'),D('100'),D('10'),start+timedelta(days=30)) for i in range(count))


def scripted(monkeypatch):
    # Mechanical lifetime fixture only; actual canonical behavior is tested separately.
    def decide(strategy,obs):
        index=int((obs.candle.open_time-flat_rows(1)[0].open_time)//HOUR)
        action=DecisionType.ENTER_LONG if index==100 and obs.position_state is PositionState.CASH else DecisionType.EXIT_LONG if index==300 and obs.position_state is PositionState.LONG else DecisionType.NO_ACTION
        return StrategyDecision(obs,action,('TEST_ONLY',),{})
    monkeypatch.setattr(engine,'evaluate_strategy',decide)


def test_long_held_position_preserves_basis_beyond_window_and_funding_ceiling(monkeypatch):
    rows=flat_rows();scripted(monkeypatch)
    r=replay(CONTEXT,rows,start=rows[99].open_time,end=rows[-1].open_time+HOUR,settings=Settings(D('500'),D('1'),D('500')))
    t=r['trades'][0]
    assert datetime.fromisoformat(t['exit_timestamp'])-datetime.fromisoformat(t['entry_timestamp'])>100*HOUR
    assert difference(D(r['metrics']['ending_cash']),D('500'))==D(t['net_pnl'])<0
    blocked=replay(CONTEXT,rows,start=rows[99].open_time,end=rows[-1].open_time+HOUR,settings=Settings(D('500'),D('5'),D('500')))
    assert blocked['counts']['research_funding_rejected']==1 and blocked['metrics']['completed_trades']==0
    assert D(blocked['metrics']['ending_cash'])==D('500')
    assert blocked['metrics']['production_risk_rejections'] is None


def test_open_end_and_determinism_and_bounds():
    r=run('open_long');assert r['metrics']['open_positions']==1 and r['open_entry'] is not None
    assert r==run('open_long')
    with pytest.raises(ValueError):Settings(D('500'),D('1'),D('500'),100001)
    with pytest.raises(ValueError):run(settings=Settings(D('10000'),D('1'),D('500'),100))
    ds=frozen_dataset('long_profit')
    with pytest.raises(ValueError):replay(ds.context,ds.candles,start=ds.candles[10].open_time,end=ds.evaluation_end,settings=Settings(D('500'),D('1'),D('500')))


def archive(tmp_path,*,unit='ms',problem=None):
    year=2024 if unit=='ms' else 2025
    start=datetime(year,1,1,tzinfo=timezone.utc);rows=[]
    for n in range(350):
        opened=start+n*HOUR;closed=opened+HOUR-timedelta(microseconds=1000 if unit=='ms' else 1)
        scale=1000 if unit=='ms' else 1
        def stamp(t):return int((t-EPOCH)//timedelta(microseconds=scale))
        rows.append([str(stamp(opened)),'100','101','99','100','10',str(stamp(closed)),'0','1','0','0','0'])
    if problem=='gap':rows.pop(40)
    if problem=='duplicate':rows.insert(40,rows[39])
    if problem=='ohlc':rows[40][3]='999'
    if problem=='ordering':rows[40],rows[41]=rows[41],rows[40]
    name=f'BTCUSDT-1h-{year}-01.zip';path=tmp_path/name
    csv_text=io.StringIO();csv.writer(csv_text).writerows(rows)
    with zipfile.ZipFile(path,'w') as z:z.writestr(name.replace('.zip','.csv'),csv_text.getvalue())
    manifest={'format':'binance-spot-archives-v1','status':'CHECKSUM_VERIFIED','files':[{'file':name,
        'url':'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/'+name,
        'sha256':data.file_hash(path),'timestamp_unit':unit,'retrieved_at':datetime(year,2,1,tzinfo=timezone.utc).isoformat()}]}
    p=tmp_path/'archives.json';data.write_json(p,manifest);return p


@pytest.mark.parametrize('unit',['ms','us'])
def test_local_archive_normalization_large_dataset_and_units(tmp_path,unit):
    manifest=archive(tmp_path,unit=unit);out=tmp_path/'normalized'
    result=data.normalize(manifest,out)
    assert result['usable'] and result['counts']['accepted']==350
    context,rows,info=data.load_dataset(out)
    assert len(rows)>300 and rows[0].close_time.microsecond==999000 and rows[0].retrieved_at.year==rows[0].open_time.year
    with pytest.raises(ValueError):data.load_dataset(out,max_candles=300)
    with (out/'candles.jsonl').open('a') as f:f.write('tampered')
    with pytest.raises(ValueError,match='hash'):data.load_dataset(out)


@pytest.mark.parametrize('problem',['gap','duplicate','ohlc','ordering'])
def test_bad_historical_data_is_quarantined_not_repaired(tmp_path,problem):
    p=archive(tmp_path,problem=problem);out=tmp_path/'normalized'
    info=data.normalize(p,out)
    assert not info['usable'] and info['rejected_examples']
    with pytest.raises(ValueError):data.load_dataset(out)
    assert p.exists() and list(tmp_path.glob('*.zip'))


def test_is_oos_and_benchmark_report_is_deterministic(tmp_path):
    manifest=archive(tmp_path);dataset=tmp_path/'dataset';data.normalize(manifest,dataset)
    _,rows,_=data.load_dataset(dataset)
    kwargs=dict(start=rows[99].open_time,split=rows[200].open_time,end=rows[-1].open_time+HOUR,settings=Settings(D('500'),D('1'),D('500')))
    r=report.evaluate(dataset,tmp_path/'report',**kwargs)
    again=report.evaluate(dataset,tmp_path/'again',**kwargs)
    assert r==again and r['status']=='COMPLETED_RESEARCH_ONLY'
    assert r['periods']['in_sample']['end']==r['periods']['out_of_sample']['start']
    assert r['periods']['full_period']['benchmarks']['CASH']['ending_equity']=='500'
    assert D(r['periods']['full_period']['benchmarks']['BUY_AND_HOLD']['ending_equity'])<D('500')
    assert r['risk_parity']['authorization']=='UNAVAILABLE_NOT_FABRICATED'
    assert r['configuration']['paper_cost_policy']=='paper-costs-v1'
    assert r['configuration']['execution']['cost_model']['trading_fee_rate']=='0.001'
    assert r['periods']['full_period']['metrics']['modeled_risk_blocked_opportunities'] is None
    for period in r['periods'].values():
        path=tmp_path/'report'/period['artifacts']['events_file']
        assert data.file_hash(path)==period['artifacts']['sha256']


def test_offline_parity_has_no_database_runner_or_production_execution_imports():
    root=Path(engine.__file__).parent
    for path in root.glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module]
        assert not any(i.startswith(('app.db','app.core','app.mandate_runner','app.execution','sqlalchemy')) for i in imports)



def test_bad_raw_hash_incomplete_rows_and_no_overwrite(tmp_path):
    p=archive(tmp_path);manifest=json.loads(p.read_text());raw=tmp_path/manifest['files'][0]['file']
    original=raw.read_bytes();raw.write_bytes(original+b'changed')
    with pytest.raises(ValueError,match='hash'):data.normalize(p,tmp_path/'bad')
    assert json.loads((tmp_path/'bad/dataset.json').read_text())['usable'] is False
    raw.write_bytes(original)
    # A completed prefix is accepted; source rows beyond its true retrieval cutoff
    # are explicitly excluded rather than backdated or interpolated.
    manifest['files'][0]['retrieved_at']=(datetime(2024,1,1,tzinfo=timezone.utc)+200*HOUR).isoformat()
    p.write_text(json.dumps(manifest))
    info=data.normalize(p,tmp_path/'cutoff')
    assert info['usable'] and info['counts']['accepted']==200 and info['counts']['INCOMPLETE_EXCLUDED']==150
    with pytest.raises(FileExistsError):data.normalize(p,tmp_path/'cutoff')


def test_download_is_explicit_bounded_and_checksums_are_verified(tmp_path,monkeypatch):
    from app.strategy_lab.historical.parity import data as module
    import httpx
    blob=b'explicit isolated download test'
    digest=hashlib.sha256(blob).hexdigest()
    requested=[]
    class Response:
        content=(digest+' fixture.zip').encode()
        text=content.decode()
        def raise_for_status(self):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_bytes(self):yield blob
    class Client:
        def __init__(self,**kw):assert kw['follow_redirects'] is False and kw['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url):requested.append(url);return Response()
        def stream(self,method,url):requested.append(url);return Response()
    monkeypatch.setattr(httpx,'Client',Client)
    m=module.download(['2024-01'],tmp_path/'download')
    assert m['status']=='CHECKSUM_VERIFIED' and len(requested)==2
    assert all(u.startswith('https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/') for u in requested)
    assert m['files'][0]['sha256']==digest and m['files'][0]['timestamp_unit']=='ms'
    with pytest.raises(ValueError):module.download(['2099-01'],tmp_path/'future')


def test_is_metrics_and_prior_decisions_unchanged_by_future_prices():
    ds=frozen_dataset('multiple');split=ds.candles[180].open_time
    settings=Settings(D('10000'),D('1'),D('500'),repeated_research_cycles=True)
    before=replay(ds.context,ds.candles,start=ds.evaluation_start,end=split,settings=settings)
    altered=(*ds.candles[:-1],replace(ds.candles[-1],close=D('250'),high=D('251')))
    after=replay(ds.context,altered,start=ds.evaluation_start,end=split,settings=settings)
    assert before==after
    assert before['counts']['eligible_evaluations']==52



def test_unknown_or_sensitive_metadata_cannot_propagate_to_reports(tmp_path):
    path=archive(tmp_path);m=json.loads(path.read_text());m['password']='SECRET'
    path.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='metadata'):data.normalize(path,tmp_path/'blocked')
    m.pop('password');m['files'][0]['url']+='?token=SECRET';path.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='URL'):data.normalize(path,tmp_path/'blocked2')
    assert not (tmp_path/'blocked').exists() and not (tmp_path/'blocked2').exists()



@pytest.mark.parametrize('kind',['long_profit','short_profit','no_trades'])
@pytest.mark.parametrize('state',[PositionState.CASH,PositionState.LONG])
def test_direct_production_canonical_observation_parity(kind,state):
    from app.risk.paper_source.market import analyze
    from app.strategies.adaptive_trend import AdaptiveTrendStrategy
    ds=frozen_dataset(kind)
    # Test-only causal retrieval stamps permit production's real-clock freshness
    # contract. Real archival imports NEVER rewrite their actual retrieval dates.
    window=tuple(replace(c,retrieved_at=c.open_time+HOUR) for c in ds.candles[-100:])
    expected,decision,_=analyze(ds.context,window,at=window[-1].open_time+HOUR+timedelta(minutes=1),position_state=state)
    observed,actual=engine.observation(ds.context,window,state,AdaptiveTrendStrategy())
    assert observed.intelligence==expected.intelligence
    assert observed.regime==expected.regime and observed.definition==expected.definition
    assert actual==decision
