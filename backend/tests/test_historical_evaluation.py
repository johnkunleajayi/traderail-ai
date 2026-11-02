"""M18 local report composition; global connection/provider guards stay enabled."""
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal, localcontext
from functools import lru_cache
import ast
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.strategy_lab.historical.input import HistoricalInput
from app.strategy_lab.historical.report import evaluate_local, json_values, LABEL
from app.strategy_lab.historical.__main__ import main
from app.strategy_lab.numeric import exact_sum, difference
from app.strategy_lab.metrics import maximum_drawdown
from test_integrated_9c_audit import frozen_dataset, AS_OF

D=Decimal


def source(kind='long_profit',split=None):
    ds=frozen_dataset(kind)
    split=split or (170 if kind=='long_profit' else 133)
    return HistoricalInput.model_validate(json_values(dict(format='traderail-historical-input-v1',
        provenance='Existing synthetic 9C '+kind+' correctness fixture',source_kind='SYNTHETIC_FIXTURE',
        instrument_id=ds.context.instrument_id,instrument='BTC/USDT',provider='BINANCE',timeframe='1h',
        warmup_start=ds.warmup_start,evaluation_start=ds.evaluation_start,evaluation_end=ds.evaluation_end,
        as_of=AS_OF,split_at=ds.candles[split].open_time,
        candles=[{k:v for k,v in asdict(c).items() if k not in ('instrument_id','provider','timeframe')}
                 for c in ds.candles])))

@lru_cache(None)
def report(kind='long_profit'):
    return evaluate_local(source(kind,split=130 if kind=='open_long' else None),initial_capital=D('10000'),quantity=D('1'))


def test_report_reuses_real_lifecycle_exact_accounting_costs_and_drawdown():
    r=report();p=r['full_period'];t=p['trades'][0]
    assert r['label']==LABEL and r['source_kind']=='SYNTHETIC_FIXTURE'
    assert r['strategy']['family']=='ADAPTIVE_TREND' and r['strategy']['version']=='1.0.0'
    assert p['metrics']['trade_count']==1 and D(t['net_pnl'])>0
    assert {'ENTER_LONG','NO_ACTION','EXIT_LONG'}<={d['decision'] for d in p['decisions']}
    assert r['configuration']['paper_cost_policy']=='paper-costs-v1'
    assert t['entry_fill']['pricing']['fee_rate']=='0.001'
    assert D(t['costs']['slippage_cost'])>0 and D(t['costs']['spread_cost'])>0
    assert difference(D(p['equity_curve'][-1]['cash']),D('10000'))==D(t['net_pnl'])
    assert difference(D(t['gross_pnl']),exact_sum(D(v) for v in t['costs'].values()))==D(t['net_pnl'])
    equities=(D('10000'),*(D(v['total_equity']) for v in p['equity_curve']))
    assert maximum_drawdown(equities)==D(p['metrics']['maximum_drawdown'])
    assert p['metrics']['annualized_return'] is None
    assert t['holding_microseconds']>0 and all(D(v['cash'])>=0 for v in p['equity_curve'])


def test_no_trade_loss_and_open_end_are_explicit():
    empty=report('no_trades')['full_period']
    assert empty['metrics']['trade_count']==0 and empty['metrics']['win_rate'] is None
    assert empty['metrics']['profit_factor'] is None and D(empty['metrics']['total_return'])==0
    loss=report('long_loss')['full_period']
    assert D(loss['trades'][0]['net_pnl'])<0 and D(loss['metrics']['maximum_drawdown'])>0
    opened=report('open_long')['full_period']
    assert opened['open_position'] is not None and opened['metrics']['trade_count']==0
    assert opened['open_position']['holding_microseconds']>0
    assert D(opened['incurred_costs']['fees'])>0
    last=opened['equity_curve'][-1]
    assert exact_sum((D(last['realized_capital']),D(last['unrealized_pnl'])))==D(last['total_equity'])


def test_determinism_and_in_sample_cannot_see_changed_oos():
    first=report();s=source()
    # Alter only a future OOS price; earlier decisions/IS must remain unchanged.
    last=s.candles[-1];future=exact_sum((last.close,D('17')))
    changed=s.model_copy(update={'candles':(*s.candles[:-1],last.model_copy(update={'close':future,'high':max(last.high,future)}))})
    second=evaluate_local(changed,initial_capital=D('10000'),quantity=D('1'))
    assert first['in_sample']==second['in_sample']
    assert first['dataset']['identity']!=second['dataset']['identity']
    assert first['full_period']['decisions'][:-1]==second['full_period']['decisions'][:-1]
    with localcontext() as c:
        c.prec=9
        assert evaluate_local(s,initial_capital=D('10000'),quantity=D('1'))==first
    assert first['in_sample']['equity_curve'][-1]['timestamp']<first['out_of_sample']['equity_curve'][0]['timestamp']
    assert first['in_sample']['initial_capital']==first['out_of_sample']['initial_capital']=='10000'


@pytest.mark.parametrize('problem',['gap','duplicate','ohlc','incomplete','future_retrieval','warmup','split','funding'])
def test_invalid_evidence_or_assumptions_fail_closed(problem):
    s=source();rows=list(s.candles);capital=D('10000')
    if problem=='gap':rows.pop(40)
    elif problem=='duplicate':rows[40]=rows[39]
    elif problem=='ohlc':rows[40]=rows[40].model_copy(update={'low':D('9999')})
    elif problem=='incomplete':s=s.model_copy(update={'as_of':s.evaluation_end-timedelta(hours=1)})
    elif problem=='future_retrieval':rows[40]=rows[40].model_copy(update={'retrieved_at':AS_OF+timedelta(days=1)})
    elif problem=='warmup':s=s.model_copy(update={'evaluation_start':rows[10].open_time})
    elif problem=='split':s=s.model_copy(update={'split_at':s.evaluation_end})
    elif problem=='funding':capital=D('1')
    with pytest.raises(ValueError):evaluate_local(s.model_copy(update={'candles':tuple(rows)}),initial_capital=capital,quantity=D('1'))


def test_input_requires_decimal_strings_and_bounded_supported_context():
    data=source().model_dump(mode='json')
    for key,value in [('instrument','ETH/USDT'),('timeframe','4h'),('unknown','client truth')]:
        with pytest.raises(ValidationError):HistoricalInput.model_validate({**data,key:value})
    data['candles'][0]['close']=1.5
    with pytest.raises(ValidationError):HistoricalInput.model_validate(data)


def test_cli_local_report_no_overwrite_and_sanitized_failure(tmp_path,monkeypatch,capsys):
    from app.strategy_lab.historical import __main__ as cli
    input_path=tmp_path/'input.json';output=tmp_path/'report.json'
    input_path.write_text(source().model_dump_json(),encoding='utf-8')
    monkeypatch.setattr(cli,'evaluate_local',lambda *args,**kwargs:report())
    args=['--input',str(input_path),'--output',str(output),'--initial-capital','10000','--quantity','1']
    assert main(args)==0 and json.loads(output.read_text(encoding='utf-8'))==report()
    original=output.read_bytes();assert main(args)==1 and output.read_bytes()==original
    input_path.write_text('SECRET TOKEN malformed',encoding='utf-8')
    assert main([*args[:3],str(tmp_path/'other.json'),*args[4:]])==1
    assert 'SECRET' not in capsys.readouterr().out


def test_historical_module_has_no_database_network_execution_or_settings_dependency():
    root=Path(__file__).parents[1]/'app/strategy_lab/historical'
    imports=[]
    for path in root.glob('*.py'):
        for n in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(n,ast.ImportFrom):imports.append(n.module or '')
            if isinstance(n,ast.Import):imports.extend(a.name for a in n.names)
    assert not any(i.startswith(('sqlalchemy','httpx','app.db','app.core','app.mandate','app.execution')) for i in imports)
