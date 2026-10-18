"""Adapters to unchanged 9A metrics; no duplicated return/statistical formulas."""
from app.strategy_lab import metrics
from app.strategy_lab.costs import CostBreakdown
from app.strategy_lab.numeric import ZERO
from .types import PerformanceResults


def trade_observations(trades):
    return tuple(metrics.RealizedTrade(trade.gross_pnl, trade.costs) for trade in trades)


def fill_cost_observations(fills):
    # Cost-only accounting projections: never passed to trade count/rate/return functions.
    return tuple(metrics.RealizedTrade(ZERO, CostBreakdown(fill.pricing.fee_amount,
        fill.pricing.spread_cost, fill.pricing.slippage_cost)) for fill in fills)


def incurred_costs(fills):
    rows = fill_cost_observations(fills)
    return CostBreakdown(metrics.total_fees(rows), metrics.total_spread_cost(rows), metrics.total_slippage_cost(rows))


def summarize(initial_capital, trades, fills, curve, exposed_duration, evaluation_duration):
    rows, costs = trade_observations(trades), fill_cost_observations(fills)
    return PerformanceResults(
        metrics.total_return(initial_capital, curve[-1].total_equity),
        metrics.gross_return(initial_capital, rows), metrics.net_return(initial_capital, rows),
        metrics.maximum_drawdown((initial_capital, *(row.total_equity for row in curve))),
        metrics.trade_count(rows), metrics.win_rate(rows), metrics.loss_rate(rows),
        metrics.profit_factor(rows), metrics.average_win(rows), metrics.average_loss(rows), metrics.expectancy(rows),
        metrics.exposure_percentage(exposed_duration, evaluation_duration),
        metrics.turnover(tuple(fill.pricing.fill_notional for fill in fills), initial_capital),
        metrics.total_fees(costs), metrics.total_spread_cost(costs), metrics.total_slippage_cost(costs))
