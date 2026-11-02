"""Rolling causal opportunity replay. No production requests or Risk approvals.
The existing research Ledger and fill pricing remain the only economic arithmetic.
"""
from collections import Counter,deque
from dataclasses import dataclass,asdict,replace
from datetime import timedelta
from decimal import Decimal
import hashlib
from pathlib import Path
from app.market_intelligence.engine import calculate_features
from app.market_intelligence.validation import validate_dataset
from app.market_regimes.engine import classify_regimes
from app.market_regimes.types import Regime
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.contract import evaluate_strategy
from app.strategies.inputs import PositionState,StrategyInput
from app.strategies.decisions import DecisionType
from app.strategy_lab.accounting.ledger import Ledger
from app.strategy_lab.accounting.performance import trade_observations
from app.strategy_lab.execution.pricing import price_fill
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration,identity
from app.strategy_lab.execution.types import SimulatedFill
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.numeric import exact_sum,exact_product,difference,ratio,require_decimal,duration_microseconds
from app.strategy_lab import metrics
from app.risk.paper_source.policies import HISTORY,COSTS,COST_VERSION
from app.strategy_lab.historical.report import json_values
from .data import bounded_limit,DEFAULT_MAX_CANDLES

HOUR=timedelta(hours=1)

@dataclass(frozen=True)
class Settings:
    capital: Decimal
    quantity: Decimal
    commitment_ceiling: Decimal
    max_candles: int=DEFAULT_MAX_CANDLES
    repeated_research_cycles: bool=False

    def __post_init__(self):
        for name in ('capital','quantity','commitment_ceiling'):
            if require_decimal(getattr(self,name),name)<=0:raise ValueError('Positive finite research settings required')
        bounded_limit(self.max_candles)
        if type(self.repeated_research_cycles) is not bool:raise ValueError('Explicit lifecycle mode required')


def costs_config():
    if (COSTS.entry_fee_rate,COSTS.entry_full_spread_rate,COSTS.entry_slippage_rate)!=(COSTS.exit_fee_rate,COSTS.exit_full_spread_rate,COSTS.exit_slippage_rate):
        raise ValueError('Unsupported asymmetric historical costs')
    return HistoricalExecutionConfiguration(ResearchCostModel(COSTS.entry_fee_rate,COSTS.entry_full_spread_rate,COSTS.entry_slippage_rate),'1h')


def observation(context,window,state,strategy):
    # Preserve archival retrieval provenance: never pretend it was fetched earlier.
    # This affects input identity, not the historical decision's candle-close time.
    observed=max(window[-1].open_time+HOUR,*(c.retrieved_at for c in window))
    features=calculate_features(context,window,as_of=observed)
    regimes=classify_regimes(features,as_of=observed)
    obs=StrategyInput(context,window[-1],features[-1],regimes[-1],strategy.definition,observed,state)
    return obs,evaluate_strategy(strategy,obs)


def allowed(regime,decision,state=PositionState.CASH):
    # Entry mirrors the mandate bridge. A held genuine EXIT_LONG follows the
    # separate production close proposal path; Risk is still explicitly unavailable.
    return regime is not Regime.UNSAFE and (regime is not Regime.UNKNOWN
        or decision is DecisionType.NO_ACTION
        or (state is PositionState.LONG and decision is DecisionType.EXIT_LONG))


def replay(context,candles,*,start,end,settings,emit=lambda event:None):
    if (context.canonical_symbol,context.provider,context.timeframe)!=('BTC/USDT','BINANCE','1h'):raise ValueError('Unsupported context')
    if len(candles)>settings.max_candles:raise ValueError('Candle limit exceeded')
    at=max(end,*(c.retrieved_at for c in candles));validate_dataset(context,candles,at)
    if not any(c.open_time==start for c in candles) or end not in tuple(c.open_time+HOUR for c in candles):raise ValueError('Exact period boundaries required')
    if sum(c.open_time<start for c in candles)<HISTORY-1 or start>=end:raise ValueError('Insufficient rolling warmup')
    strategy=AdaptiveTrendStrategy();config=costs_config();ledger=Ledger(settings.capital)
    window=deque(maxlen=HISTORY);pending=None;terminal=False
    counts=Counter({name:0 for name in ('period_hours','eligible_evaluations','cash_evaluations','holding_evaluations',
        'valid_unknown','unsafe_regime','unknown_action_rejected','no_action','cash_probe_long_signals',
        'long_entry_signals','long_signals_while_holding','hold','exit_long_signals','simulated_entries',
        'simulated_exits','research_funding_rejected','unsupported_direction_or_state','terminal_barrier_hours','end_of_data_unfilled')})
    reasons=Counter();regime_counts=Counter();checks={}
    equity=[settings.capital];utilization=[];exposed=0;chain='';fills=0;last=None;peak=settings.capital
    def event(value):
        nonlocal chain
        clean=json_values(value);chain=identity('historical-parity-event/v1',{'previous':chain,'event':clean});emit({**clean,'event_identity':chain})
    for candle in candles:
        if candle.open_time>=end:break
        window.append(candle)
        if candle.open_time<start:continue
        counts['period_hours']+=1
        if pending is not None:
            decision,prefix,regime=pending;pending=None
            pricing=price_fill(candle.open,decision.decision,settings.quantity,config.cost_model)
            commitment=exact_sum((pricing.fill_notional,pricing.fee_amount))
            if decision.decision is DecisionType.ENTER_LONG and (commitment>ledger.cash or commitment>settings.commitment_ceiling):
                counts['research_funding_rejected']+=1
                event({'kind':'FUNDING_REJECTED','time':candle.open_time,'reason':'MODELED_CASH_OR_COMMITMENT_LIMIT','decision_identity':decision.decision_identity,'required_cash':commitment})
            else:
                following=PositionState.LONG if decision.decision is DecisionType.ENTER_LONG else PositionState.CASH
                fill_id=identity('historical-parity-fill/v1',{'decision':decision.decision_identity,'time':candle.open_time,'pricing':asdict(pricing),'quantity':settings.quantity})
                fill=SimulatedFill(prefix,decision.decision_identity,decision,context,strategy.definition.family,strategy.definition.version,
                    strategy.definition.configuration_identity,decision.timestamp,candle.open_time,ledger.state,following,
                    decision.decision,settings.quantity,pricing,config,fill_id)
                ledger.apply(fill);fills+=1
                counts['simulated_entries' if following is PositionState.LONG else 'simulated_exits']+=1
                event({'kind':'RESEARCH_FILL_NOT_AUTHORIZED','fill':fill})
                if following is PositionState.CASH and not settings.repeated_research_cycles:terminal=True
        prefix=identity('historical-parity-window/v1',{'context':json_values(context),'window':json_values(tuple(window))})
        if terminal:
            counts['terminal_barrier_hours']+=1
            event({'kind':'TERMINAL_BARRIER','time':candle.close_time})
        else:
            counts['eligible_evaluations']+=1
            obs,decision=observation(context,tuple(window),ledger.state,strategy)
            regime=obs.regime.regime;regime_counts[regime.value]+=1
            counts['cash_evaluations' if ledger.state is PositionState.CASH else 'holding_evaluations']+=1
            if regime is Regime.UNKNOWN:counts['valid_unknown' if allowed(regime,decision.decision,ledger.state) else 'unknown_action_rejected']+=1
            if regime is Regime.UNSAFE:counts['unsafe_regime']+=1
            if decision.decision is DecisionType.NO_ACTION:counts['no_action']+=1
            reasons.update(decision.reason_codes)
            # A separate CASH observation diagnoses opportunities while holding;
            # it can never schedule an entry or alter the managed position.
            cash_obs=obs if ledger.state is PositionState.CASH else replace(obs,position_state=PositionState.CASH)
            cash_decision=decision if ledger.state is PositionState.CASH else evaluate_strategy(strategy,cash_obs)
            if allowed(cash_obs.regime.regime,cash_decision.decision):
                if cash_decision.decision is DecisionType.ENTER_LONG:
                    counts['cash_probe_long_signals']+=1
                    if ledger.state is PositionState.LONG:counts['long_signals_while_holding']+=1
                if cash_obs.regime.regime is Regime.BULL_TREND:
                    for name,passed in cash_decision.evidence.get('entry_confirmations',{}).items():
                        tally=checks.setdefault(name,Counter());tally['observed']+=1;tally['passed' if passed else 'failed']+=1
            if not allowed(regime,decision.decision,ledger.state):
                classification='UNSAFE_OR_ACTIONABLE_UNKNOWN_REJECTED'
            elif decision.decision is DecisionType.ENTER_LONG and ledger.state is PositionState.CASH:
                counts['long_entry_signals']+=1;classification='STRATEGY_OPPORTUNITY_RISK_UNAVAILABLE'
                pending=(decision,prefix,regime)
            elif decision.decision is DecisionType.EXIT_LONG and ledger.state is PositionState.LONG:
                counts['exit_long_signals']+=1;classification='STRATEGY_EXIT_RISK_UNAVAILABLE';pending=(decision,prefix,regime)
            elif decision.decision is DecisionType.NO_ACTION:
                classification='HOLD' if ledger.state is PositionState.LONG else 'NO_OPPORTUNITY'
                if ledger.state is PositionState.LONG:counts['hold']+=1
            else:
                classification='UNSUPPORTED_DIRECTION_OR_STATE';counts['unsupported_direction_or_state']+=1
            event({'kind':'EVALUATION','timestamp':decision.timestamp,'observed_at':obs.as_of,
                'window_first':window[0].open_time,'window_count':len(window),'prefix_identity':prefix,
                'position':ledger.state,'regime':regime,'regime_reasons':obs.regime.evidence.reason_codes,
                'decision':decision,'classification':classification,
                'cash_probe_decision':cash_decision.decision,'cash_probe_reasons':cash_decision.reason_codes})
        totals,realized,capital,q,unrealized,value=ledger.mark(candle.close);equity.append(value)
        if ledger.state is PositionState.LONG:exposed+=1
        utilization.append(ratio(exact_product(q,candle.close),settings.capital))
        peak=max(peak,value)
        event({'kind':'EQUITY','time':candle.close_time,'cash':ledger.cash,'quantity':q,
            'marked_exposure':exact_product(q,candle.close),'equity':value,'realized_pnl':realized,
            'unrealized_pnl':unrealized,'drawdown_fraction':ratio(difference(peak,value),peak),
            'capital_utilization_fraction':utilization[-1]})
        last=candle
    if last is None:raise ValueError('No evaluation rows')
    if pending is not None:
        counts['end_of_data_unfilled']+=1
        event({'kind':'UNFILLED','reason':'END_OF_DATASET','decision_identity':pending[0].decision_identity})
    trade_rows=trade_observations(ledger.trades);totals=metrics.realized_pnl_aggregation(trade_rows)
    stat=dict(completed_trades=len(ledger.trades),open_positions=int(ledger.entry is not None),
        gross_realized_pnl=totals.gross_pnl,net_realized_pnl=totals.net_pnl,
        unrealized_pnl=ledger.mark(last.close)[4],ending_cash=ledger.cash,ending_equity=equity[-1],
        net_return=metrics.total_return(settings.capital,equity[-1]),win_rate=metrics.win_rate(trade_rows),
        profit_factor=metrics.profit_factor(trade_rows),expectancy=metrics.expectancy(trade_rows),
        maximum_drawdown=metrics.maximum_drawdown(equity),modeled_fees=ledger.fees,
        modeled_spread=ledger.spread,modeled_slippage=ledger.slippage,
        exposure_percentage=ratio(Decimal(exposed*100),Decimal(counts['period_hours'])),
        mean_capital_utilization=ratio(exact_sum(utilization),Decimal(len(utilization))),
        average_holding_microseconds=ratio(Decimal(sum(duration_microseconds(t.exit_timestamp-t.entry_timestamp,'holding') for t in ledger.trades)),Decimal(len(ledger.trades))),
        long_signal_frequency=ratio(Decimal(counts['long_entry_signals']),Decimal(counts['eligible_evaluations'])),
        entry_fill_frequency=ratio(Decimal(counts['simulated_entries']),Decimal(counts['eligible_evaluations'])),
        completed_trades_per_30_days=ratio(Decimal(len(ledger.trades)*720),Decimal(counts['period_hours'])),
        production_authorized_trades=0,production_risk_rejections=None,
        modeled_risk_blocked_opportunities=None)
    return json_values({'start':start,'end':end,'metrics':stat,'counts':dict(counts),'reason_counts':dict(reasons),
        'regime_counts':dict(regime_counts),
        'long_entry_condition_checks':{name:{**dict(tally),'pass_rate':ratio(Decimal(tally['passed']),Decimal(tally['observed'])),
            'fail_rate':ratio(Decimal(tally['failed']),Decimal(tally['observed']))} for name,tally in checks.items()},
        'condition_coverage':'Only canonical LONG entry_confirmations explicitly present in BULL_TREND CASH-probe decisions; absent checks are not assumed failed',
        'trades':ledger.trades,'open_entry':ledger.entry,'event_chain_identity':chain,
        'assessment':'DESCRIPTIVE_ONLY; selectivity cannot be judged without sample/context and Risk-parity evidence'})
