"""M18.3 offline-only correctness fixtures, never real-market performance evidence."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext
from pathlib import Path
from copy import deepcopy
import ast,json
import pytest
from app.strategy_lab.historical.report import json_values
from app.strategy_lab.historical.parity.data import normalize,load_dataset,file_hash,write_json
from app.strategy_lab.historical.parity.engine import Settings,costs_config
from app.strategy_lab.historical.parity.report import evaluate,source_version
from app.strategy_lab.historical.robustness import benchmarks,evidence,attribution,statistics,unknown
from app.strategy_lab.historical.robustness.analysis import study
from app.strategy_lab.historical.robustness.risk_contract import feasibility,FAITHFUL,MODELED,UNAVAILABLE
from app.strategy_lab.historical.robustness.__main__ import main
from app.strategy_lab.execution.config import identity
from app.strategy_lab.validation import TimeWindow
from app.strategy_lab.numeric import exact_sum,difference
from app.market_regimes.classification import classify_evidence
from app.market_regimes.config import REGIME_CONFIG
from app.market_regimes.types import Regime
from test_integrated_9c_audit import frozen_dataset,pipeline
from test_historical_parity import archive
D=Decimal

@pytest.fixture(scope='module')
def economic():
    ds=frozen_dataset('long_profit')
    result=pipeline('long_profit',costs=costs_config().cost_model)
    t=json_values(result.completed_trades[0]);index={c.open_time:i for i,c in enumerate(ds.candles)}
    return ds,t,index

@pytest.fixture(scope='module')
def local(tmp_path_factory):
    root=tmp_path_factory.mktemp('robustness');manifest=archive(root)
    normalize(manifest,root/'dataset');ctx,rows,info=load_dataset(root/'dataset')
    r=evaluate(root/'dataset',root/'parent',start=rows[99].open_time,split=rows[200].open_time,
        end=rows[-1].open_time+timedelta(hours=1),settings=Settings(D('500'),D('.005'),D('500'),repeated_research_cycles=True))
    protocol={'version':'m18-3-predeclared-v1','baseline_source':source_version(),
        'strategy_configuration_identity':r['strategy']['configuration_identity'],
        'settings':{'capital':'500','quantity':'0.005','commitment_ceiling':'500','repeated_research_cycles':True},
        'existing_2024_report_sha256':file_hash(root/'parent/report.json'),'fresh_periods':[]}
    protocol['protocol_identity']=identity('m18-3-protocol/v1',protocol);write_json(root/'protocol.json',protocol)
    return root,ctx,rows,info,r,protocol


def test_trade_attribution_cash_costs_and_exit_bar_excursions(economic):
    ds,t,index=economic;row=attribution.trade_attribution(t,ds.candles,index)
    assert row['net_pnl']==D(t['net_pnl'])
    assert difference(row['gross_pnl'],exact_sum((row['fees'],row['spread'],row['slippage'])))==row['net_pnl']
    assert row['entry_fee']==D(t['entry_fill']['pricing']['fee_amount'])
    a=index[datetime.fromisoformat(t['entry_timestamp'])];b=index[datetime.fromisoformat(t['exit_timestamp'])]
    assert row['maximum_favorable_excursion']==max(D(0),max(c.high for c in ds.candles[a:b])-row['entry_fill_price'])*row['quantity']
    changed=list(ds.candles);changed[b]=replace(changed[b],high=D('999999'),low=D('.01'))
    assert attribution.trade_attribution(t,tuple(changed),index)==row
    bad=deepcopy(t);bad['net_pnl']='999'
    with pytest.raises(ValueError):attribution.trade_attribution(bad,ds.candles,index)


def test_group_totals_and_repricing_reconcile(economic):
    ds,t,index=economic;row=attribution.trade_attribution(t,ds.candles,index)
    for groups in attribution.grouped([row]).values():assert exact_sum(v['net_pnl'] for v in groups.values())==row['net_pnl']
    s=attribution.cost_sensitivity([row])['scenarios']
    assert s['1']['net_completed_pnl']==row['net_pnl']
    assert s['0']['net_completed_pnl']==row['gross_pnl']
    assert s['2']['net_completed_pnl']<s['1']['net_completed_pnl']<s['0.5']['net_completed_pnl']
    with localcontext() as c:
        c.prec=9;assert attribution.cost_sensitivity([row])['scenarios']==s


@pytest.mark.parametrize('values,expected',[((),None),((D(3),),D(3)),((D(1),D(3)),D(2)),((D(1),D(5),D(9)),D(5))])
def test_statistics_edges(values,expected):
    assert statistics.median(values)==expected
    if not values:assert statistics.dispersion(values) is None
    if len(values)==1:assert statistics.dispersion(values)==0
    empty=statistics.trade_statistics([])
    assert empty['win_rate'] is None and empty['independent_trade_count'] is None
    assert empty['win_rate_interval'] is None


def test_zero_exposure_benchmark_is_cash_and_never_buys_btc(local,monkeypatch):
    root,ctx,rows,info,r,protocol=local
    from app.strategy_lab.dataset import create_dataset
    ds=create_dataset(ctx,rows,warmup_start=rows[0].open_time,evaluation_start=rows[99].open_time,
        evaluation_end=rows[-1].open_time+timedelta(hours=1),as_of=rows[-1].retrieved_at)
    calls=[];original=benchmarks.benchmark_window
    def observed(*a,**kw):calls.append(a[4].kind.value);return original(*a,**kw)
    monkeypatch.setattr(benchmarks,'benchmark_window',observed)
    window=TimeWindow(ds.evaluation_start,ds.evaluation_end)
    b=benchmarks.passive(ds,window,capital=D(500),budget=D(0),as_of=rows[-1].retrieved_at)
    assert calls==['CASH'] and b['quantity']==0 and b['costs'].total==0
    assert b['ending_equity']==500 and b['net_return']==0 and b['mean_marked_exposure']==0 and b['time_exposure_percentage']==0
    points=[(c.close_time,D(500)) for c in rows[99:]]
    all_b=benchmarks.compare_benchmarks(ds,window,capital=D(500),quantity=D('.005'),ceiling=D(500),
        strategy_exposure=[D(0)]*len(points),strategy_curve=points,as_of=rows[-1].retrieved_at)
    assert all_b['ex_post_exposure_matched_buy_hold']['quantity']==0
    assert all_b['fixed_quantity_capital_constrained_buy_hold']['quantity']==D('.005')
    assert benchmarks.passive(ds,window,capital=D(500),budget=D(-1),as_of=rows[-1].retrieved_at)['state']=='UNAVAILABLE'


def test_verified_parent_and_immutable_baseline(local,tmp_path):
    root,ctx,rows,info,r,protocol=local
    loaded,check=evidence.verify_report(root/'parent/report.json',info,protocol)
    assert check['material_source_match'] and check['baseline_fingerprint'].startswith('sha256:')
    bad=deepcopy(r);key=next(iter(evidence.material_hashes(bad['source'])))
    physical=next(p for p in bad['source']['source_hashes'] if p.replace(chr(92),'/')==key)
    bad['source']['source_hashes'][physical]='0'*64
    bad.pop('report_identity');bad['report_identity']=identity('historical-parity-report/v1',bad);write_json(tmp_path/'bad.json',bad)
    with pytest.raises(ValueError,match='baseline'):evidence.verify_report(tmp_path/'bad.json',info,protocol)
    bad=deepcopy(protocol);bad['settings']['quantity']='10'
    with pytest.raises(ValueError):evidence.checked_identity(bad,'protocol_identity','m18-3-protocol/v1')


def test_event_hash_and_canonical_window_fail_closed(local,tmp_path):
    root,ctx,rows,info,r,protocol=local;p=r['periods']['full_period']
    events=list(evidence.events(root/'parent',p))
    e=next(e for e in events if e['kind']=='EVALUATION');m=evidence.Measurements(ctx,rows)
    first=m.at(e);assert len(m.cache)==1
    assert m.at(e)==first and len(m.cache)==1
    changed=list(rows);changed[-1]=replace(changed[-1],close=D(200),high=D(201))
    assert evidence.Measurements(ctx,tuple(changed)).at(e)==first  # No future-data leakage.
    bad=deepcopy(e);bad['prefix_identity']='sha256:'+'0'*64
    with pytest.raises(ValueError):m.at(bad)
    filename=p['artifacts']['events_file'];(tmp_path/filename).write_text('{}\n')
    with pytest.raises(ValueError,match='hash'):list(evidence.events(tmp_path,p))


def test_unknown_predicates_reasons_and_period_forward_boundary(local):
    root,ctx,rows,info,r,protocol=local;p=r['periods']['full_period'];m=evidence.Measurements(ctx,rows)
    observations=[m.at(e) for e in evidence.events(root/'parent',p) if e['kind']=='EVALUATION']
    result=unknown.summarize_regimes(observations,rows,m.index,datetime.fromisoformat(p['end']))
    assert result['counts']==p['regime_counts']
    assert sum(result['unknown_primary_reason_counts'].values())==result['counts'].get('UNKNOWN',0)
    for _,regime,e in observations[:3]:
        snapshot=unknown.predicate_snapshot(e);candidate,_=classify_evidence(e,REGIME_CONFIG)
        if candidate is Regime.RANGE:assert snapshot['range'] is True
    # Controlled UNKNOWN confirmation evidence, no fabricated production label.
    _,_,e=observations[0]
    mocked=[(rows[99].open_time,Regime.UNKNOWN,replace(e,reason_codes=('CONFIRMATION_PENDING',)))]
    end=rows[100].open_time
    x=unknown.summarize_regimes(mocked,rows,m.index,end)
    assert x['unknown_reason_counts']=={'CONFIRMATION_PENDING':1}
    assert x['forward_moves']['24h']['UNKNOWN']['all_anchors_overlapping']['returns']['count']==0


def test_complete_analysis_deterministic_and_no_period_crossing(local):
    root,ctx,rows,info,r,protocol=local
    first=study('2024H1',root/'dataset',root/'parent/report.json',protocol)
    second=study('2024H1',root/'dataset',root/'parent/report.json',protocol)
    assert json_values(first)==json_values(second)
    assert first['periods']['in_sample']['end']==first['periods']['out_of_sample']['start']
    assert all(p['accounting_verified'] for p in first['periods'].values())
    assert first['periods']['full_period']['statistics']['independent_trade_count'] is None


def test_cli_report_and_risk_specification(local,tmp_path,capsys):
    root,ctx,rows,info,r,protocol=local;out=tmp_path/'report'
    args=['--protocol',str(root/'protocol.json'),'--study','2024H1',str(root/'dataset'),str(root/'parent/report.json'),'--output',str(out)]
    assert main(args)==0
    report=json.loads((out/'research.json').read_text(encoding='utf-8'))
    evidence.checked_identity(report,'analysis_identity','m18-3-research/v1')
    assert main(args)==1 and (out/'research.md').exists()
    f=feasibility();assert f['approval_creation'] is False and f['production_permission'] is False
    assert set(f['mandatory_gates'])=={'TRADE_RISK','PORTFOLIO_RISK','RISK_STATE','SAFETY_GATE'}
    controls={c['control']:c['classification'] for c in f['controls']}
    assert controls['six_kill_switch_scopes']==UNAVAILABLE
    assert controls['completed_market_candles']==FAITHFUL and controls['position_sizing']==MODELED


def test_modules_are_offline_and_have_no_production_write_dependencies():
    root=Path(evidence.__file__).parent
    for p in root.glob('*.py'):
        tree=ast.parse(p.read_text(encoding='utf-8'))
        imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module]
        assert not any(m.startswith(('app.db','app.core','app.execution','app.mandate_runner','sqlalchemy','httpx')) for m in imports)



def test_rejected_dataset_cannot_be_relabelled_for_analysis(local,tmp_path):
    root,ctx,rows,info,r,protocol=local
    bad=tmp_path/'rejected';bad.mkdir();rejected=deepcopy(info);rejected['usable']=False
    write_json(bad/'dataset.json',rejected)
    with pytest.raises(ValueError):study('2024H1',bad,root/'parent/report.json',protocol)


def test_predeclared_missing_period_must_be_explicitly_rejected(local,tmp_path):
    root,ctx,rows,info,r,protocol=local;p=deepcopy(protocol)
    p['fresh_periods']=[{'name':'2023H1','raw_start':'2023-01-01T00:00:00Z',
        'evaluation_start':'2023-01-05T03:00:00Z','split':'2023-04-01T00:00:00Z','end':'2023-07-01T00:00:00Z'}]
    p.pop('protocol_identity');p['protocol_identity']=identity('m18-3-protocol/v1',p);write_json(tmp_path/'protocol.json',p)
    out=tmp_path/'gap-report'
    args=['--protocol',str(tmp_path/'protocol.json'),'--study','2024H1',str(root/'dataset'),str(root/'parent/report.json'),'--output',str(out)]
    assert main(args)==1 and not out.exists()
    rejected={'usable':False,'counts':{'accepted':4342,'INVALID_ROW':1,'MISSING_INTERVAL':1,'missing_intervals':2},
        'first_open':'2023-01-01T00:00:00+00:00','last_open':'2023-06-30T23:00:00+00:00','normalized_sha256':'0'*64}
    write_json(tmp_path/'rejected.json',rejected)
    assert main([*args,'--excluded-dataset',str(tmp_path/'rejected.json')])==0
    result=json.loads((out/'research.json').read_text(encoding='utf-8'))
    assert result['status']=='COMPLETED_WITH_EXPLICIT_DATA_GAPS'
    assert result['excluded'][0]['name']=='2023H1' and len(result['studies'])==1



def test_recomputed_outer_hash_cannot_hide_corrupt_event_chain(local,tmp_path):
    root,ctx,rows,info,r,protocol=local;p=deepcopy(r['periods']['full_period'])
    original=root/'parent'/p['artifacts']['events_file']
    lines=original.read_text(encoding='utf-8').splitlines();first=json.loads(lines[0]);first['kind']='TAMPERED'
    lines[0]=json.dumps(first)
    target=tmp_path/p['artifacts']['events_file'];target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    p['artifacts']['sha256']=file_hash(target)
    with pytest.raises(ValueError,match='chain'):list(evidence.events(tmp_path,p))
