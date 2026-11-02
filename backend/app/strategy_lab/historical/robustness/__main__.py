"""Finite offline robustness report from verified existing backtest artifacts."""
import argparse
import json
import re
from datetime import datetime,timedelta
from pathlib import Path
from app.strategy_lab.execution.config import identity
from app.strategy_lab.historical.parity.data import write_json,file_hash
from app.strategy_lab.historical.report import json_values,LABEL
from .evidence import read_json,checked_identity
from .analysis import study
from .risk_contract import feasibility


def hypotheses():
    return [
        {'name':'Cost-aware opportunity quality','mechanism':'Test whether marginal entries have sufficient modeled edge to cover canonical costs; no relaxation of entry thresholds',
         'motivation':'Compare observed gross/net gap and fixed-path repricing, not assumed future edge',
         'downside':'May exclude winners and increase selection bias',
         'test':'One frozen research-only variant on new predeclared non-overlapping holdouts; never retune April–June 2024',
         'criterion':'Net return improves and maximum drawdown does not worsen in BOTH fresh holdouts; fewer than 20 completed trades total is descriptively inconclusive, not a significance threshold',
         'snooping_risk':'Choosing a cutoff on observed OOS would invalidate the test'},
        {'name':'Exit capture versus normal path variation','mechanism':'Test one predeclared exit alternative only after inspecting loss-after-MFE and holding bands',
         'motivation':'Positive intrabar MFE before a losing close is a bound, not proof that an earlier sale was attainable',
         'downside':'Earlier exits can truncate rare large winners and increase turnover',
         'test':'Identical entries, shared adverse execution/accounting, one isolated exit hypothesis on new untouched holdouts',
         'criterion':'Net expectancy improves in both holdouts without higher drawdown or an unexplained increase in modeled costs',
         'snooping_risk':'Do not select exit parameters from the best historical excursion'},
        {'name':'Regime ambiguity as information, not an error','mechanism':'Diagnose conflicting structure/momentum and confirmation pending before considering a separately versioned interpretation',
         'motivation':'Use exact UNKNOWN reasons/predicates and dependent forward-move diagnostics; fewer UNKNOWN labels is not success',
         'downside':'Reducing ambiguity can admit whipsaws and unsupported trades',
         'test':'One predeclared research variant only; baseline classifier remains immutable',
         'criterion':'Positive cost-adjusted improvement in both new holdouts with no drawdown increase; label-count changes alone fail',
         'snooping_risk':'Forward moves are hindsight, and overlapping observations cannot establish predictive significance'}]


def markdown(report):
    lines=['# M18.3 Strategy Robustness Research', '',LABEL,'',
        'No production Risk approvals or live-money readiness are asserted. All returns are simulated.',
        '', '## Verified baseline findings', '',
        '| Study | Period | Trades | Net P&L USDT | Net return | Max drawdown |',
        '|---|---|---:|---:|---:|---:|']
    for s in report['studies']:
        for name,p in s['periods'].items():
            m=p['baseline_metrics'];lines.append('| '+s['name']+' | '+name+' | '+str(m['completed_trades'])+' | '+m['net_realized_pnl']+' | '+m['net_return']+' | '+m['maximum_drawdown']+' |')
    lines.extend(['','Returns and drawdowns above are fractions. Independent period ledgers cannot be added.',
        '', '## Confirmed versus hypotheses', '',
        'Canonical fill/cost/cash reconciliation, source windows and event hashes were checked. Trade excursions are OHLC bounds excluding the exit bar, not executable prices.',
        'UNKNOWN reasons and independent predicate support are diagnostics from the canonical classifier. Branch precedence and labels are never replaced.',
        'Exposure matching is EX POST, uses future strategy exposure, and is not deployable or perfectly risk equivalent.',
        'Completed trades are not assumed independent. No confidence intervals, p-values or profitability significance are claimed.',
        'A losing close after favorable excursion does not establish poor exit timing. Entries/exits require controlled independent experiments.',
        '', '## Rejected periods', ''])
    for e in report['excluded']:
        lines.append(str(e['first_open'])+' to '+str(e['last_open'])+': rejected; '+json.dumps(e['counts'])+'. No gap fill, shortened replacement period or performance result.')
    for study in report['studies']:
        p=study['periods']['full_period'];total=p['totals'];u=p['unknown_analysis'];stat=p['statistics']
        lines.extend(['','## '+study['name']+' detailed findings','',
            'CONFIRMED arithmetic: gross completed P&L '+total['gross_pnl']+', fees '+total['fees']+', spread '+total['spread']+', slippage '+total['slippage']+', net '+total['net_pnl']+' USDT.',
            'Losing trades with positive favorable OHLC excursion: '+str(total['loss_after_favorable_excursion'])+'. This does not prove attainable exit prices or late exits.',
            'UNKNOWN primary reasons (disjoint): '+json.dumps(u['unknown_primary_reason_counts'])+'. Supporting reason counts in JSON are multi-label.',
            'All observed rolling candidates/retained measurements were revalidated against canonical functions. No classifier threshold was changed.',
            '', '| Holding band | Trades | Gross P&L | Net P&L |','|---|---:|---:|---:|'])
        for band,g in p['groups']['holding_band'].items():lines.append('| '+band+' | '+str(g['trades'])+' | '+g['gross_pnl']+' | '+g['net_pnl']+' |')
        lines.extend(['','Holding duration is an outcome, not information available at entry. These groups do not justify forcing longer holds.',
            '', '| Comparator | Net return | Max drawdown | Time exposure % |','|---|---:|---:|---:|'])
        for name,b in p['benchmarks'].items():
            if name=='strategy':continue
            lines.append('| '+name+' | '+str(b.get('net_return'))+' | '+str(b.get('maximum_drawdown'))+' | '+str(b.get('time_exposure_percentage'))+' |')
        lines.extend(['','The exposure-matched comparator is ex post and not deployable. Daily return dispersion and actual mean exposures are in JSON; matching an average amount does not equal matching risk.',
            'Completed trade count: '+str(stat['completed_trade_count'])+'; independent count and win-rate uncertainty interval: UNAVAILABLE.',
            'Mean net return on entry commitment: '+str(stat['net_return_on_entry_commitment']['mean'])+'; median: '+str(stat['net_return_on_entry_commitment']['median'])+'.',
            'Best/worst 1/3/5-trade concentration is reported against absolute P&L contribution, not a misleading near-zero net denominator.',
            '', '| Cost multiplier | Fixed-path net completed P&L | Profit factor |','|---|---:|---:|'])
        for multiple,scenario in p['cost_sensitivity']['scenarios'].items():lines.append('| '+multiple+' | '+scenario['net_completed_pnl']+' | '+str(scenario['profit_factor'])+' |')
        lines.extend(['','These sensitivity figures reprice the same references/quantities using canonical pricing. They do not re-evaluate funding, Risk, sizing or decisions and are not new executable backtests.'])
    lines.extend(['','## Risk replay', '',
        'Allocation, sizing, account state and authorization age require explicit modeled inputs. Operational freshness, six kill switches and lifecycle/health need unavailable historical attestations.',
        'All four production gates remain mandatory. See research.json risk_feasibility for the source-linked contract; no historical approval service was implemented.',
        '', '## Proposed experiments', ''])
    for h in report['hypotheses']:lines.extend(['### '+h['name'],h['mechanism'],h['test'],h['criterion'],h['downside'],''])
    lines.extend(['## Remaining limitations','',
        'Fresh 2022 and existing 2024 are not a comprehensive independent-regime sample. The 2023 source gap prevents the full predeclared study.',
        'Fixed quantity, research next-open fills, no impact/liquidity model and missing production Risk remain material limitations.',
        'M18.4 should predeclare fresh untouched holdouts, resolve source completeness without interpolation, and review one hypothesis at a time. Do not loosen production thresholds.'])
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=LABEL)
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--study',nargs=3,action='append',metavar=('NAME','DATASET','REPORT'),required=True)
    parser.add_argument('--excluded-dataset',type=Path,action='append',default=[])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.output.exists():raise ValueError('Output exists')
        protocol=read_json(args.protocol);checked_identity(protocol,'protocol_identity','m18-3-protocol/v1')
        if protocol['version']!='m18-3-predeclared-v1' or len(args.study)>4:raise ValueError('Unsupported/bounded protocol')
        if len({s[0] for s in args.study})!=len(args.study) or any(not re.fullmatch(r'20[0-9]{2}H1',s[0]) for s in args.study):raise ValueError('Distinct bounded study names required')
        if len(args.excluded_dataset)>len(protocol['fresh_periods']):raise ValueError('Excluded dataset bound')
        excluded=[]
        for path in args.excluded_dataset:
            e=read_json(path)
            if e.get('usable') is not False:raise ValueError('Only rejected datasets can be excluded')
            name=str(datetime.fromisoformat(e['first_open']).year)+'H1'
            declared=next((d for d in protocol['fresh_periods'] if d['name']==name),None)
            if declared is None or datetime.fromisoformat(e['first_open'])!=datetime.fromisoformat(declared['raw_start'].replace('Z','+00:00')) or datetime.fromisoformat(e['last_open'])+timedelta(hours=1)!=datetime.fromisoformat(declared['end'].replace('Z','+00:00')):raise ValueError('Excluded period not predeclared')
            excluded.append({**{k:e[k] for k in ('counts','first_open','last_open','normalized_sha256')},'name':name,'rejected_artifact_sha256':file_hash(path)})
        fresh={d['name'] for d in protocol['fresh_periods']}
        covered={s[0] for s in args.study if s[0] in fresh}|{e['name'] for e in excluded}
        if covered!=fresh or len({e['name'] for e in excluded})!=len(excluded) or {s[0] for s in args.study}&{e['name'] for e in excluded}:raise ValueError('Every predeclared period must be uniquely analyzed or explicitly rejected')
        args.output.mkdir(parents=True)
        result={'label':LABEL,'version':'m18-3-research-v1','status':'INCOMPLETE','protocol':protocol,
            'analysis_source_hashes':{p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
            'studies':[],'excluded':excluded,'risk_feasibility':feasibility(),'hypotheses':hypotheses()}
        try:
            for name,dataset,path in args.study:result['studies'].append(study(name,dataset,path,protocol))
            if len({s['integrity']['baseline_fingerprint'] for s in result['studies']})!=1:raise ValueError('Cross-study baseline changed')
            result['status']='COMPLETED_WITH_EXPLICIT_DATA_GAPS' if excluded else 'COMPLETED_RESEARCH_ONLY'
            result=json_values(result);result['analysis_identity']=identity('m18-3-research/v1',result)
        except Exception as error:
            result['status']='FAILED';result['failure_category']=type(error).__name__;write_json(args.output/'research.json',result);raise
        write_json(args.output/'research.json',result)
        (args.output/'research.md').write_text(markdown(result),encoding='utf-8')
        print('PASS: M18.3 RESEARCH REPORT; HISTORICAL SIMULATION ONLY');return 0
    except Exception as error:
        print('STOP: M18_3_RESEARCH_FAILED; category='+type(error).__name__);return 1

if __name__=='__main__':raise SystemExit(main())
