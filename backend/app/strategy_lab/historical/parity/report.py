"""Local composition. Detailed records stream to bounded JSONL artifacts."""
from dataclasses import asdict
from datetime import datetime,timedelta
import json
from pathlib import Path
import subprocess
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.benchmarks import BenchmarkDefinition,BenchmarkKind
from app.strategy_lab.benchmark_evaluation.engine import benchmark_window
from app.strategy_lab.validation import TimeWindow
from app.strategy_lab.execution.config import identity
from app.strategy_lab.historical.report import LABEL,json_values
from app.risk.paper_source.policies import HISTORY,COST_VERSION
from .data import load_dataset,file_hash,write_json
from .engine import replay,costs_config


def source_version():
    root=Path(__file__).resolve().parents[5]
    paths=set(Path(__file__).parent.glob('*.py'))
    backend=root/'backend/app'
    for folder in ('market_intelligence','market_regimes','strategies/adaptive_trend',
                   'strategy_lab/accounting','strategy_lab/execution'):
        paths.update((backend/folder).glob('*.py'))
    paths.update(backend/p for p in ('strategy_lab/metrics.py','strategy_lab/numeric.py',
        'strategy_lab/costs.py','strategy_lab/benchmark_evaluation/engine.py',
        'risk/paper_source/policies.py','risk/paper_source/market.py','mandate_strategy/pipeline.py'))
    paths=sorted(paths)
    result={'source_hashes':{str(p.relative_to(root)):file_hash(p) for p in paths}}
    try:result['git_head']=subprocess.run(['git','-c','safe.directory='+str(root),'rev-parse','HEAD'],cwd=root,capture_output=True,text=True,timeout=3,check=True).stdout.strip()
    except Exception:result['git_head']='UNVERIFIED'
    try:result['working_tree_dirty']=bool(subprocess.run(['git','-c','safe.directory='+str(root),'status','--porcelain'],cwd=root,capture_output=True,text=True,timeout=3,check=True).stdout.strip())
    except Exception:result['working_tree_dirty']=None
    return result


def evaluate(directory,output,*,start,split,end,settings):
    context,candles,info=load_dataset(directory,max_candles=settings.max_candles)
    if not start<split<end:raise ValueError('Chronological nonempty IS/OOS windows required')
    data=create_dataset(context,candles,warmup_start=candles[0].open_time,evaluation_start=start,
        evaluation_end=candles[-1].open_time+timedelta(hours=1),as_of=datetime.fromisoformat(info['as_of']))
    if start<candles[HISTORY-1].open_time or end>data.evaluation_end:raise ValueError('Rolling warmup/coverage unavailable')
    windows={'full_period':(start,end),'in_sample':(start,split),'out_of_sample':(split,end)}
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    report={'label':LABEL,'version':'historical-parity-v1','status':'INCOMPLETE','source':source_version(),
        'dataset':info,'configuration':json_values({**asdict(settings),'execution':costs_config().configuration(),'paper_cost_policy':COST_VERSION}),
        'strategy':{'family':AdaptiveTrendStrategy().definition.family,'version':AdaptiveTrendStrategy().definition.version,
            'configuration_identity':AdaptiveTrendStrategy().definition.configuration_identity},
        'parity':{'rolling_candles':HISTORY,'direction':'LONG_CASH','canonical_intelligence_regime_strategy':True,
            'unknown_no_action':'NO_TRADE','unsafe':'REJECTED','first_completed_close_terminal':not settings.repeated_research_cycles,
            'execution':'RESEARCH_NEXT_OPEN_NOT_PRODUCTION_CLOSE_PRICE','sizing':'FIXED_RESEARCH_QUANTITY_NOT_PRODUCTION_RISK'},
        'risk_parity':{'authorization':'UNAVAILABLE_NOT_FABRICATED',
            'TRADE_RISK':'NOT_REPLAYED; fixed quantity is not potential-loss Risk sizing',
            'PORTFOLIO_RISK':'NOT_REPLAYED; authoritative historical portfolio/strategy attribution unavailable',
            'RISK_STATE':'NOT_REPLAYED; authoritative genesis/transition provenance unavailable',
            'SAFETY_GATE':'NOT_REPLAYED; historical kill-switch/lifecycle/health attestations unavailable',
            'modeled_funding':'Research pricing plus actual simulated cash and fee-inclusive ceiling; NOT a RiskDecision'},
        'units':'Financial values Decimal strings; returns/frequencies/drawdown/utilization fractions; exposure_percentage percent',
        'denominators':{'signal_frequency':'LONG entry proposals / eligible managed hourly evaluations',
            'trade_frequency':'completed trades * 720 / period hours','entry_frequency':'research entry fills / eligible evaluations',
            'cash_probe':'counterfactual CASH while holding; never execution',
            'condition_rates':'Canonical LONG entry_confirmations explicitly observed; absent checks are not failed'},
        'split':json_values({'start':start,'split':split,'end':end,'state':'Independent CASH/capital per period; analytics-only warmup; no position/fill carry'}),
        'warnings':['HISTORICAL RESULTS DO NOT ESTABLISH LIVE-MONEY READINESS',
            'No production approvals, operational freshness or approval-expiry attestations fabricated',
            'Archival observation includes original retrieval date; close decisions and next-open fills are retrospective, not real-time availability proof',
            'No order-book/impact/latency model; open positions marked, never forcibly liquidated',
            'First-cycle mode stops after one close; repeated mode is explicitly NON-PRODUCTION research',
            'Benchmarks enter first period open, include entry costs and final close marks; no hypothetical exit costs',
            'Buy-and-hold uses full fee-inclusive capital; fixed-quantity strategy exposure differs',
            'Small-sample metrics are descriptive; no threshold recommendation, OOS optimization or annualized-profit claims'],
        'periods':{}}
    try:
        for name,(left,right) in windows.items():
            print('PROGRESS: '+name,flush=True)
            path=output/(name+'-events.jsonl')
            with path.open('x',encoding='utf-8') as stream:
                def emit(event):stream.write(json.dumps(event,separators=(',',':'),allow_nan=False)+'\n')
                period=replay(context,candles,start=left,end=right,settings=settings,emit=emit)
            period['artifacts']={'events_file':path.name,'sha256':file_hash(path)};period['benchmarks']={}
            for kind in (BenchmarkKind.BUY_AND_HOLD,BenchmarkKind.CASH):
                baseline=benchmark_window(data,TimeWindow(left,right),settings.capital,costs_config(),BenchmarkDefinition(kind),as_of=datetime.fromisoformat(info['as_of']))
                bpath=output/(name+'-'+kind.value.lower()+'.json');write_json(bpath,baseline)
                period['benchmarks'][kind.value]={'artifact':bpath.name,'sha256':file_hash(bpath),
                    'ending_equity':str(baseline.ending_equity),'net_return':str(baseline.total_return),
                    'maximum_drawdown':str(baseline.maximum_drawdown),'exposure_percentage':str(baseline.exposure_percentage),
                    'costs':json_values(baseline.costs)}
            report['periods'][name]=period
        report['status']='COMPLETED_RESEARCH_ONLY';report['report_identity']=identity('historical-parity-report/v1',json_values(report))
    except Exception as error:
        report['status']='FAILED';report['failure_category']=type(error).__name__;write_json(output/'report.json',report);raise
    write_json(output/'report.json',report)
    return report
