"""Transparent constrained passive comparators, reusing existing benchmark accounting."""
from decimal import Decimal
from app.strategies.decisions import DecisionType
from app.strategy_lab.benchmark_evaluation.engine import benchmark_window
from app.strategy_lab.benchmarks import BenchmarkDefinition,BenchmarkKind
from app.strategy_lab.execution.pricing import price_fill
from app.strategy_lab.historical.parity.engine import costs_config
from app.strategy_lab.numeric import exact_sum,exact_product,difference,ratio
from app.strategy_lab import metrics
from .statistics import describe,daily_returns

D=Decimal


def passive(data,window,*,capital,budget,as_of,target_quantity=None):
    if budget<0:return {'state':'UNAVAILABLE','reason':'NEGATIVE_EXPOSURE_BUDGET'}
    if budget>capital:return {'state':'UNAVAILABLE','reason':'BUDGET_EXCEEDS_CAPITAL'}
    b=benchmark_window(data,window,capital if budget==0 else budget,costs_config(),BenchmarkDefinition(BenchmarkKind.CASH if budget==0 else BenchmarkKind.BUY_AND_HOLD),as_of=as_of)
    if target_quantity is not None and b.quantity!=target_quantity:raise ValueError('Fixed-unit benchmark quantity mismatch')
    idle=D(0) if budget==0 else difference(capital,budget)
    curve=tuple((o.timestamp,exact_sum((idle,o.equity))) for o in b.observations)
    exposure=tuple(exact_product(b.quantity,o.close) for o in b.observations)
    return {'state':'AVAILABLE','initial_capital':capital,'commitment_budget':budget,'quantity':b.quantity,
        'ending_equity':curve[-1][1],'net_return':metrics.total_return(capital,curve[-1][1]),
        'maximum_drawdown':metrics.maximum_drawdown((capital,*(e for _,e in curve))),
        'mean_marked_exposure':ratio(exact_sum(exposure),D(len(exposure))),
        'time_exposure_percentage':D(100) if b.quantity else D(0),'costs':b.costs,
        'daily_returns':describe(daily_returns(curve)),
        'pricing':'Predeclared first-open entry, canonical entry fee/spread/slippage, final close mark; no fictitious exit fee',
        'return_denominator':'Entire initial research capital, including idle cash'}


def compare_benchmarks(data,window,*,capital,quantity,ceiling,strategy_exposure,strategy_curve,as_of):
    full=passive(data,window,capital=capital,budget=capital,as_of=as_of)
    unit=price_fill(next(c.open for c in data.candles if c.open_time==window.start),DecisionType.ENTER_LONG,quantity,costs_config().cost_model)
    commitment=exact_sum((unit.fill_notional,unit.fee_amount))
    constrained=passive(data,window,capital=capital,budget=commitment,as_of=as_of,target_quantity=quantity) if commitment<=min(capital,ceiling) else {'state':'UNAVAILABLE','reason':'FIXED_QUANTITY_EXCEEDS_CEILING'}
    target=ratio(exact_sum(strategy_exposure),D(len(strategy_exposure)))
    factor=ratio(target,full['mean_marked_exposure'])
    budget=min(capital,ceiling,exact_product(capital,factor)) if factor is not None else D(0)
    matched=passive(data,window,capital=capital,budget=budget,as_of=as_of)
    matched.update({'method':'EX_POST_MEAN_MARKED_EXPOSURE_MATCH; informed by entire strategy window; diagnostic, NOT deployable',
        'target_mean_marked_exposure':target,'risk_equivalence':False,
        'limitation':'Matches average amount, not timing, volatility, tail risk or independent investment policy; capped at capital/ceiling',
        'mean_exposure_difference':difference(matched['mean_marked_exposure'],target) if matched['state']=='AVAILABLE' else None})
    return {'strategy':{'daily_returns':describe(daily_returns(strategy_curve)),'mean_marked_exposure':target},
        'cash':{'initial_capital':capital,'ending_equity':capital,'net_return':D(0),'maximum_drawdown':D(0),'time_exposure_percentage':D(0),'daily_return_stddev':D(0)},
        'full_buy_hold':full,'fixed_quantity_capital_constrained_buy_hold':constrained,'ex_post_exposure_matched_buy_hold':matched}
