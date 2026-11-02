"""Compose existing pure research engines; reporting never grants trading authority."""
from dataclasses import asdict, fields, is_dataclass
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategy_lab.replay import replay_dataset
from app.strategy_lab.execution import simulate_execution
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration, identity
from app.strategy_lab.accounting import account_simulation
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.validation import TimeWindow, ValidationSplit
from app.strategy_lab.out_of_sample import OutOfSamplePlan, validate_out_of_sample
from app.strategy_lab.metrics import drawdown_series
from app.strategy_lab.numeric import exact_sum, exact_product, ratio, duration_microseconds, require_decimal
from app.risk.paper_source.policies import COSTS, COST_VERSION
from .input import HistoricalInput

LABEL='HISTORICAL SIMULATION — NOT LIVE OR FORWARD PERFORMANCE'


def json_values(value):
    if isinstance(value,Decimal):return format(value,'f')
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,timedelta):return duration_microseconds(value,'report duration')
    if isinstance(value,Enum):return value.value
    if isinstance(value,UUID):return str(value)
    if is_dataclass(value):return {field.name:json_values(getattr(value,field.name)) for field in fields(value)}
    if isinstance(value,Mapping):return {key:json_values(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [json_values(item) for item in value]
    if value is None or isinstance(value,(str,int,bool)):return value
    raise TypeError('Unsupported report value')


def period_report(result):
    curve=result.equity_curve;initial=result.initial_capital
    drawdowns=drawdown_series((initial,*(row.total_equity for row in curve)))[1:]
    durations=[duration_microseconds(t.exit_timestamp-t.entry_timestamp,'holding') for t in result.completed_trades]
    utilization=tuple(ratio(exact_product(row.quantity,row.mark_price),initial) for row in curve)
    trades=[]
    for trade,duration in zip(result.completed_trades,durations):
        trades.append({**json_values(trade),'holding_microseconds':duration})
    stats=asdict(result.performance)
    stats.update(net_realized_pnl=result.completed_trade_totals.net_pnl,
        gross_profit=exact_sum(t.gross_pnl for t in result.completed_trades if t.gross_pnl>0),
        gross_loss=exact_sum(t.gross_pnl.copy_abs() for t in result.completed_trades if t.gross_pnl<0),
        annualized_return=None,annualized_return_reason='INSUFFICIENT_OBSERVATION_PERIOD',
        average_holding_microseconds=ratio(Decimal(sum(durations)),Decimal(len(durations))),
        mean_capital_utilization=ratio(exact_sum(utilization),Decimal(len(utilization))),
        maximum_capital_utilization=max(utilization),
        unavailable_reasons={name:'ZERO_DENOMINATOR_OR_NO_COMPLETED_TRADES'
            for name,value in asdict(result.performance).items() if value is None})
    open_position=json_values(result.open_position) if result.open_position else None
    if open_position is not None:
        open_position['holding_microseconds']=duration_microseconds(
            result.replay.evaluation_end-result.open_position.entry_fill.eligible_fill_timestamp,'open holding')
    return dict(result_identity=result.result_identity,replay_identity=result.replay.replay_identity,
        dataset_fingerprint=result.dataset_fingerprint,initial_capital=initial,
        evaluation_start=result.replay.evaluation_start,evaluation_end=result.replay.evaluation_end,
        warmup_candles=result.replay.warmup_count,metrics=stats,
        completed_trade_totals=asdict(result.completed_trade_totals),
        incurred_costs=asdict(result.incurred_costs),trades=trades,open_position=open_position,
        equity_curve=[{**asdict(row),'drawdown_fraction':dd,'capital_utilization_fraction':u}
            for row,dd,u in zip(curve,drawdowns,utilization)],
        decisions=[{'timestamp':r.decision.timestamp,'decision':r.decision.decision,
            'reason_codes':r.decision.reason_codes,'position_before':r.position_before,
            'position_after':r.position_after,'decision_identity':r.decision.decision_identity,
            'prefix_identity':r.dataset_prefix_fingerprint,'regime':r.regime.regime} for r in result.replay.records],
        fills=[json_values(fill) for fill in result.execution.fills],
        unfilled_decisions=[json_values(row) for row in result.execution.unfilled_decisions])


def evaluate_local(source: HistoricalInput,*,initial_capital: Decimal,quantity: Decimal):
    for name,value in (('initial_capital',initial_capital),('quantity',quantity)):
        if require_decimal(value,name)<=0:raise ValueError('Positive research capital and quantity required')
    dataset=source.dataset();strategy=AdaptiveTrendStrategy()
    # Existing research model is symmetric. Refuse an unsupported future policy change.
    if (COSTS.entry_fee_rate,COSTS.entry_full_spread_rate,COSTS.entry_slippage_rate)!=(
            COSTS.exit_fee_rate,COSTS.exit_full_spread_rate,COSTS.exit_slippage_rate):
        raise ValueError('Asymmetric Paper costs need an explicit research model')
    costs=ResearchCostModel(COSTS.entry_fee_rate,COSTS.entry_full_spread_rate,COSTS.entry_slippage_rate)
    execution_config=HistoricalExecutionConfiguration(costs,'1h')
    plan=OutOfSamplePlan(dataset,TimeWindow(dataset.warmup_start,dataset.evaluation_start),
        ValidationSplit(TimeWindow(dataset.evaluation_start,source.split_at),TimeWindow(source.split_at,dataset.evaluation_end)),
        strategy.definition,execution_config,quantity,initial_capital)
    # Freeze split/configuration before any evaluations. No optimization or fitting.
    replay=replay_dataset(dataset,strategy,as_of=source.as_of)
    execution=simulate_execution(dataset,replay,quantity=quantity,cost_model=costs,as_of=source.as_of)
    full=account_simulation(dataset,replay,execution,initial_capital=initial_capital,as_of=source.as_of)
    paired=validate_out_of_sample(plan,AdaptiveTrendStrategy,as_of=source.as_of)
    report=dict(label=LABEL,report_version='historical-evaluation-v1',currency='USDT',
        source_kind=source.source_kind,provenance=source.provenance,
        dataset=dict(identity=dataset.dataset_fingerprint,context=asdict(dataset.context),
            warmup_start=dataset.warmup_start,evaluation_start=dataset.evaluation_start,
            evaluation_end=dataset.evaluation_end,as_of=source.as_of,candle_count=dataset.candle_count),
        strategy=dict(family=strategy.definition.family,version=strategy.definition.version,
            configuration_identity=strategy.definition.configuration_identity),
        configuration=dict(plan_identity=plan.plan_identity,policy=plan.configuration(),paper_cost_policy=COST_VERSION),
        assumptions=dict(sizing='EXPLICIT_FIXED_BASE_QUANTITY_NOT_RISK_SIZING',direction='EXISTING_LONG_SHORT_RESEARCH_MODEL',
            execution='NEXT_CANDLE_OPEN_ADVERSE_SPREAD_SLIPPAGE_AND_FEES',
            allocation='NO_MANDATE_OR_PORTFOLIO_RESERVATION',
            units='Returns/win rate/drawdown/utilization are fractions; exposure_percentage is percent; durations are microseconds',
            is_oos='Independent CASH starts; IS prefix warms OOS analytics only; no position or pending-fill carry',
            decision_time='Candle close; causal prefix observed at max(completion, prefix retrieval timestamps)',
            utilization='Uniform hourly gross marked exposure divided by initial capital'),
        warnings=['NOT_RISK_AUTHORIZED; research does not reproduce autonomous sizing or permission',
            'No parameter optimization; small samples do not establish predictive reliability',
            'Research uses expanding analytical prefixes; autonomous Paper uses a rolling 100-candle observation',
            'Archival next-open pricing is retrospective, not proof of data availability or achievable execution',
            'No order-book liquidity, latency or market-impact model; positions are not force-closed at dataset end',
            '300-candle local-input limit; annualization unavailable for this short observation window'],
        full_period=period_report(full),in_sample=period_report(paired.in_sample),
        out_of_sample=period_report(paired.out_of_sample),comparison=[asdict(row) for row in paired.comparison])
    if source.source_kind=='SYNTHETIC_FIXTURE':report['warnings'].append('SYNTHETIC DATA: correctness demonstration only, never real performance evidence')
    report['report_identity']=identity('historical-evaluation-report/v1',json_values(report))
    return json_values(report)
