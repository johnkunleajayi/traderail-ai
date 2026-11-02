"""Strict trade attribution from canonical simulated fills and original candles."""
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from app.strategies.decisions import DecisionType
from app.market_regimes.config import REGIME_CONFIG
from app.strategy_lab.costs import CostBreakdown
from app.strategy_lab import metrics
from app.strategy_lab.execution.pricing import price_fill
from app.strategy_lab.historical.parity.engine import costs_config
from app.strategy_lab.numeric import exact_sum,exact_product,difference,ratio,duration_microseconds
from .statistics import trade_statistics

D=Decimal


def atr_band(value):
    if value is None:return 'UNAVAILABLE'
    v=D(value)
    if v<REGIME_CONFIG.breakout_atr_floor:return 'ATR_BELOW_BREAKOUT_FLOOR'
    if v<REGIME_CONFIG.high_volatility_atr_floor:return 'ATR_BETWEEN_CONFIG_FLOORS'
    return 'ATR_AT_OR_ABOVE_HIGH_VOL_FLOOR'


def holding_band(hours):
    if hours<=24:return 'LE_24H'
    if hours<=72:return 'GT_24H_LE_72H'
    if hours<=168:return 'GT_72H_LE_168H'
    return 'GT_168H'


def trade_attribution(trade,candles,index):
    entry,exit=trade['entry_fill'],trade['exit_fill']
    if entry['action']!='ENTER_LONG' or exit['action']!='EXIT_LONG':raise ValueError('LONG completed trade required')
    begin=datetime.fromisoformat(trade['entry_timestamp']);end=datetime.fromisoformat(trade['exit_timestamp'])
    if begin not in index or end not in index or begin>=end:raise ValueError('Complete entry/exit interval required')
    a,b=index[begin],index[end];held=candles[a:b]  # Exit occurs at open: exit candle high/low MUST NOT leak in.
    if not held or len(held)*3600000000!=duration_microseconds(end-begin,'holding'):raise ValueError('Complete held-candle history required')
    q=D(trade['quantity']);ep=entry['pricing'];xp=exit['pricing']
    if q<=0 or not q.is_finite():raise ValueError('Positive exact quantity required')
    if D(ep['reference_open'])!=candles[a].open or D(xp['reference_open'])!=candles[b].open:raise ValueError('Trade references do not match original candles')
    if q!=D(entry['quantity']) or q!=D(exit['quantity']):raise ValueError('Quantity binding mismatch')
    fees=exact_sum((D(ep['fee_amount']),D(xp['fee_amount'])))
    spread=exact_sum((D(ep['spread_cost']),D(xp['spread_cost'])))
    slippage=exact_sum((D(ep['slippage_cost']),D(xp['slippage_cost'])))
    gross=exact_product(q,difference(D(xp['reference_open']),D(ep['reference_open'])))
    net=difference(gross,exact_sum((fees,spread,slippage)))
    if (gross!=D(trade['gross_pnl']) or net!=D(trade['net_pnl'])
        or (fees,spread,slippage)!=(D(trade['costs']['fees']),D(trade['costs']['spread_cost']),D(trade['costs']['slippage_cost']))):
        raise ValueError('Canonical trade economics do not reconcile')
    if (D(ep['fill_notional'])!=exact_product(q,D(ep['fill_price']))
        or D(xp['fill_notional'])!=exact_product(q,D(xp['fill_price']))):raise ValueError('Notional mismatch')
    fill_net=exact_sum((D(xp['fill_notional']),D(ep['fill_notional']).copy_negate(),fees.copy_negate()))
    if fill_net!=net:raise ValueError('Cash P&L reconciliation mismatch')
    reference=D(ep['fill_price']);mfe=exact_product(q,max(D(0),difference(max(c.high for c in held),reference)))
    mae=exact_product(q,min(D(0),difference(min(c.low for c in held),reference)))
    duration=duration_microseconds(end-begin,'holding');ev=entry['decision']['evidence'];xv=exit['decision']['evidence']
    commitment=exact_sum((D(ep['fill_notional']),D(ep['fee_amount'])))
    return {'trade_identity':trade['trade_identity'],'entry_time':begin,'exit_time':end,'quantity':q,
        'entry_reference_price':D(ep['reference_open']),'exit_reference_price':D(xp['reference_open']),
        'entry_fill_price':reference,'exit_fill_price':D(xp['fill_price']),
        'entry_fee':D(ep['fee_amount']),'exit_fee':D(xp['fee_amount']),
        'entry_slippage':D(ep['slippage_cost']),'exit_slippage':D(xp['slippage_cost']),
        'gross_pnl':gross,'net_pnl':net,'fees':fees,'spread':spread,'slippage':slippage,
        'capital_committed':commitment,'net_return_on_commitment':ratio(net,commitment),
        'cost_burden_on_commitment':ratio(exact_sum((fees,spread,slippage)),commitment),
        'holding_microseconds':duration,'holding_band':holding_band(ratio(D(duration),D(3600000000))),
        'maximum_favorable_excursion':mfe,'maximum_adverse_excursion':mae,
        'excursion_basis':'Held-candle high/low versus adverse entry fill, excluding exit bar; bounds only, not achievable exit prices; exit fees excluded',
        'loss_after_favorable_excursion':net<0 and mfe>0,
        'entry_regime':ev.get('regime','UNAVAILABLE'),'exit_regime':xv.get('regime','UNAVAILABLE'),
        'entry_volatility_band':atr_band(ev.get('normalized_atr')),'entry_signal_evidence':ev,
        'exit_reason_codes':exit['decision']['reason_codes'],
        'entry_calendar_month':begin.strftime('%Y-%m'),'exit_calendar_month':end.strftime('%Y-%m'),
        'outcome':'WIN' if net>0 else 'LOSS' if net<0 else 'FLAT'}


def aggregate(rows):
    return {'trades':len(rows),'gross_pnl':exact_sum(r['gross_pnl'] for r in rows),
        'net_pnl':exact_sum(r['net_pnl'] for r in rows),'fees':exact_sum(r['fees'] for r in rows),
        'spread':exact_sum(r['spread'] for r in rows),'slippage':exact_sum(r['slippage'] for r in rows),
        'loss_after_favorable_excursion':sum(r['loss_after_favorable_excursion'] for r in rows)}


def grouped(rows):
    dimensions=('entry_regime','exit_regime','holding_band','entry_volatility_band','outcome','entry_calendar_month','exit_calendar_month')
    output={}
    for dimension in dimensions:
        groups=defaultdict(list)
        for row in rows:groups[row[dimension]].append(row)
        output[dimension]={name:aggregate(items) for name,items in sorted(groups.items())}
    return output


def cost_sensitivity(trades):
    """Reprice fixed reference-price trades using shared pricing, never rerun decisions."""
    base=costs_config().cost_model;output={}
    for multiplier in (D('0'),D('0.5'),D('1'),D('2')):
        model=replace(base,trading_fee_rate=exact_product(base.trading_fee_rate,multiplier),
            spread_rate=exact_product(base.spread_rate,multiplier),slippage_rate=exact_product(base.slippage_rate,multiplier))
        observations=[]
        for t in trades:
            q=D(t['quantity']);entry=price_fill(D(t['entry_reference_price']),DecisionType.ENTER_LONG,q,model)
            exit=price_fill(D(t['exit_reference_price']),DecisionType.EXIT_LONG,q,model)
            costs=CostBreakdown(exact_sum((entry.fee_amount,exit.fee_amount)),exact_sum((entry.spread_cost,exit.spread_cost)),exact_sum((entry.slippage_cost,exit.slippage_cost)))
            observations.append(metrics.RealizedTrade(D(t['gross_pnl']),costs))
        totals=metrics.realized_pnl_aggregation(observations)
        output[str(multiplier)]={'net_completed_pnl':totals.net_pnl,'gross_pnl':totals.gross_pnl,
            'fees':totals.fees,'spread':totals.spread_cost,'slippage':totals.slippage_cost,
            'win_rate':metrics.win_rate(observations),'profit_factor':metrics.profit_factor(observations),'expectancy':metrics.expectancy(observations)}
    return {'method':'FIXED_TRADE_PATH_REPRICING; no Risk/sizing/funding/decision re-evaluation; not a new executable backtest or equity curve','scenarios':output}
