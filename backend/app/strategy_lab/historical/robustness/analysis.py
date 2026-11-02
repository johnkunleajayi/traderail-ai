"""Read-only M18.3 research composition; no new strategy/backtesting engine."""
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
from collections import Counter
from app.strategy_lab.historical.parity.data import load_dataset,file_hash,write_json
from app.strategy_lab.historical.report import json_values,LABEL
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.validation import TimeWindow
from app.strategy_lab.execution.pricing import price_fill
from app.strategy_lab.historical.parity.engine import costs_config
from app.strategies.decisions import DecisionType
from app.strategy_lab.numeric import exact_sum,exact_product,difference
from app.strategy_lab import metrics
from .evidence import read_json,checked_identity,verify_report,events,Measurements
from .attribution import trade_attribution,aggregate,grouped,cost_sensitivity
from .statistics import trade_statistics
from .benchmarks import compare_benchmarks
from .unknown import summarize_regimes
from .risk_contract import feasibility

D=Decimal
HOUR=timedelta(hours=1)


def analyze_period(directory,period,data,measurements,settings):
    begin=datetime.fromisoformat(period['start']);end=datetime.fromisoformat(period['end'])
    index=measurements.index;observations=[];curve=[];exposure=[];fills={};counts=Counter()
    for event in events(directory,period,limit=len(data.candles)):
        kind=event['kind'];counts[kind]+=1
        if kind=='EVALUATION':
            point=measurements.at(event)
            if not begin<=point[0]<end:raise ValueError('Evaluation outside period')
            observations.append(point)
        elif kind=='EQUITY':
            time=datetime.fromisoformat(event['time']);opened=time.replace(minute=0,second=0,microsecond=0)
            if opened not in index or not begin<=opened<end or data.candles[index[opened]].close_time!=time:raise ValueError('Equity period/source mismatch')
            marked=exact_product(D(event['quantity']),data.candles[index[opened]].close)
            value=D(event['equity']);cash=D(event['cash'])
            if marked!=D(event['marked_exposure']) or exact_sum((cash,marked))!=value:raise ValueError('Equity cash/exposure mismatch')
            curve.append((time,value));exposure.append(marked)
        elif kind=='RESEARCH_FILL_NOT_AUTHORIZED':
            fill=event['fill'];key=fill['fill_identity']
            if key in fills:raise ValueError('Duplicate research fill')
            time=datetime.fromisoformat(fill['eligible_fill_timestamp'])
            if time not in index or not begin<=time<end:raise ValueError('Fill outside historical period')
            action=DecisionType(fill['action'])
            if action not in (DecisionType.ENTER_LONG,DecisionType.EXIT_LONG):raise ValueError('Unexpected direction')
            pricing=price_fill(data.candles[index[time]].open,action,D(fill['quantity']),costs_config().cost_model)
            if json_values(pricing)!=fill['pricing']:raise ValueError('Canonical fill pricing mismatch')
            fills[key]=fill
    expected_times=[c.close_time for c in data.candles if begin<=c.open_time<end]
    if [t for t,_ in curve]!=expected_times:raise ValueError('Incomplete/nonchronological equity evidence')
    if counts['EVALUATION']!=period['counts']['eligible_evaluations']:raise ValueError('Evaluation count mismatch')
    rows=[];used=[]
    for trade in period['trades']:
        for leg in ('entry_fill','exit_fill'):
            f=trade[leg]
            if fills.get(f['fill_identity'])!=f:raise ValueError('Trade not backed by exact fill')
            used.append(f['fill_identity'])
        rows.append(trade_attribution(trade,data.candles,index))
    opened=period['open_entry']
    if opened is not None:
        if fills.get(opened['fill_identity'])!=opened:raise ValueError('Open basis lacks exact fill')
        used.append(opened['fill_identity'])
    if len(used)!=len(set(used)) or set(used)!=set(fills):raise ValueError('Unattributed or reused fill')
    total=aggregate(rows);stat=period['metrics'];capital=D(settings['capital'])
    if total['net_pnl']!=D(stat['net_realized_pnl']) or total['gross_pnl']!=D(stat['gross_realized_pnl']):raise ValueError('Completed P&L mismatch')
    open_fee=D(opened['pricing']['fee_amount']) if opened else D(0)
    expected=exact_sum((capital,total['net_pnl'],D(stat['unrealized_pnl']),open_fee.copy_negate()))
    if expected!=curve[-1][1] or expected!=D(stat['ending_equity']):raise ValueError('Ending accounting mismatch')
    if metrics.maximum_drawdown((capital,*(v for _,v in curve)))!=D(stat['maximum_drawdown']):raise ValueError('Drawdown mismatch')
    sensitivity=cost_sensitivity(rows)
    if sensitivity['scenarios']['1']['net_completed_pnl']!=total['net_pnl']:raise ValueError('Baseline cost repricing mismatch')
    benchmarks=compare_benchmarks(data,TimeWindow(begin,end),capital=capital,quantity=D(settings['quantity']),
        ceiling=D(settings['commitment_ceiling']),strategy_exposure=exposure,strategy_curve=curve,as_of=max(c.retrieved_at for c in data.candles))
    return {'start':begin,'end':end,'baseline_metrics':stat,'event_counts':dict(counts),'accounting_verified':True,
        'trade_attribution':rows,'totals':total,'groups':grouped(rows),'statistics':trade_statistics(rows),
        'cost_sensitivity':sensitivity,'unknown_analysis':summarize_regimes(observations,data.candles,index,end),
        'benchmarks':benchmarks,'interpretation':{
            'confirmed':'Price/cost/cash attribution is arithmetic over recorded fills; no trade-path changes',
            'hypothesis_only':'Loss after positive intrabar excursion may motivate exit experiments; it does not prove a late exit or achievable high-price sale'}}


def study(name,dataset,report_path,protocol):
    context,candles,info=load_dataset(dataset)
    report,integrity=verify_report(report_path,info,protocol)
    split=report['split'];start=datetime.fromisoformat(split['start']);end=datetime.fromisoformat(split['end'])
    declared=next((p for p in protocol['fresh_periods'] if p['name']==name),None)
    if declared is not None:
        for key,field in (('start','evaluation_start'),('split','split'),('end','end')):
            if datetime.fromisoformat(split[key])!=datetime.fromisoformat(declared[field].replace('Z','+00:00')):raise ValueError('Fresh period protocol mismatch')
    elif name!='2024H1' or integrity['parent_report_sha256']!=protocol['existing_2024_report_sha256']:
        raise ValueError('Study not in frozen protocol')
    data=create_dataset(context,candles,warmup_start=candles[0].open_time,evaluation_start=start,
        evaluation_end=candles[-1].open_time+HOUR,as_of=datetime.fromisoformat(info['as_of']))
    measured=Measurements(context,candles);periods={}
    for period_name,period in report['periods'].items():
        print('PROGRESS: '+name+' '+period_name,flush=True)
        for b in period['benchmarks'].values():
            path=Path(report_path).parent/b['artifact']
            if Path(b['artifact']).name!=b['artifact'] or file_hash(path)!=b['sha256']:raise ValueError('Benchmark artifact hash mismatch')
        periods[period_name]=analyze_period(Path(report_path).parent,period,data,measured,protocol['settings'])
    return {'name':name,'status':'ANALYZED','integrity':integrity,'fresh_predeclared':declared is not None,
        'dataset_integrity':{'sha256':info['normalized_sha256'],'counts':info['counts'],'first_open':info['first_open'],'last_open':info['last_open'],
            'source_manifest':info['source_manifest']},'periods':periods,
        'prior_oos_limit':'April–June 2024 remains observed baseline evidence, never a tuning target' if name=='2024H1' else 'Fresh evaluation is now observed; future experiments require new untouched holdouts'}
