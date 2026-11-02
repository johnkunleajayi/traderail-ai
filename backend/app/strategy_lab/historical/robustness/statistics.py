"""Descriptive Decimal statistics. No IID claims, significance tests or tuning."""
from datetime import datetime,timedelta
from decimal import Decimal,Context,localcontext
from app.strategy_lab.numeric import exact_sum,exact_product,difference,ratio

D=Decimal


def mean(values):
    rows=tuple(values)
    return ratio(exact_sum(rows),D(len(rows)))


def median(values):
    rows=sorted(values);n=len(rows)
    if not n:return None
    return rows[n//2] if n%2 else ratio(exact_sum((rows[n//2-1],rows[n//2])),D(2))


def dispersion(values):
    rows=tuple(values);average=mean(rows)
    if not rows:return None
    variance=mean(exact_product(difference(x,average),difference(x,average)) for x in rows)
    with localcontext(Context(prec=50)):return variance.sqrt()


def describe(values):
    rows=tuple(values)
    return {'count':len(rows),'mean':mean(rows),'median':median(rows),
        'population_stddev':dispersion(rows),'minimum':min(rows) if rows else None,'maximum':max(rows) if rows else None}


def daily_returns(points):
    """Adjacent UTC closing-day equities only; first partial-day return excluded."""
    closes={}
    for time,equity in points:
        if time.hour==23:closes[time.date()]=equity
    result=[]
    for day,value in sorted(closes.items()):
        previous=closes.get(day-timedelta(days=1))
        if previous is not None and previous>0:result.append(ratio(difference(value,previous),previous))
    return tuple(result)


def trade_statistics(trades):
    pnl=tuple(D(t['net_pnl']) for t in trades)
    returns=tuple(ratio(D(t['net_pnl']),D(t['capital_committed'])) for t in trades)
    absolute=exact_sum(v.copy_abs() for v in pnl)
    concentration={}
    for k in (1,3,5):
        best=sorted(pnl,reverse=True)[:k];worst=sorted(pnl)[:k]
        concentration[str(k)]={'selected':min(k,len(pnl)),'best_net_sum':exact_sum(best),'worst_net_sum':exact_sum(worst),
            'absolute_contribution_best':ratio(exact_sum(v.copy_abs() for v in best),absolute),
            'absolute_contribution_worst':ratio(exact_sum(v.copy_abs() for v in worst),absolute),
            'net_without_best':difference(exact_sum(pnl),exact_sum(best)),'net_without_worst':difference(exact_sum(pnl),exact_sum(worst))}
    return {'completed_trade_count':len(trades),'independent_trade_count':None,
        'independence_reason':'Sequential non-overlapping positions do not establish independent trade outcomes',
        'win_rate':ratio(D(sum(v>0 for v in pnl)),D(len(pnl))),
        'win_rate_interval':None,'interval_reason':'Time dependence is unmodelled; no IID interval, p-value or significance claim',
        'net_pnl_distribution':describe(pnl),'net_return_on_entry_commitment':describe(returns),
        'concentration':concentration,'sufficiency':'DESCRIPTIVE_ONLY; independent-regime replication and predeclared validation required'}
