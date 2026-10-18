"""Equal-weight descriptive OOS evidence, never a continuous portfolio or verdict."""
from dataclasses import dataclass
from decimal import Decimal
from app.strategy_lab.accounting.types import PerformanceResults
from app.strategy_lab.numeric import exact_sum, ratio, require_decimal


def return_sign(value):
    if value is None:
        return None
    return "POSITIVE" if value > 0 else "NEGATIVE" if value < 0 else "ZERO"


def _mean(values):
    return ratio(exact_sum(values), Decimal(len(values))) if values else None


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else _mean(tuple(ordered[middle-1:middle+1]))


@dataclass(frozen=True, slots=True)
class WalkForwardSummary:
    fold_count: int
    profitable_folds: int
    losing_folds: int
    breakeven_folds: int
    undefined_return_folds: int
    mean_net_return: Decimal | None
    median_net_return: Decimal | None
    worst_net_return: Decimal | None
    best_net_return: Decimal | None
    median_maximum_drawdown: Decimal | None
    worst_maximum_drawdown: Decimal | None
    defined_drawdown_folds: int
    total_completed_trades: int
    zero_trade_folds: int
    mean_expectancy: Decimal | None
    defined_expectancy_folds: int
    mean_profit_factor: Decimal | None
    defined_profit_factor_folds: int
    total_fees: Decimal
    total_spread_cost: Decimal
    total_slippage_cost: Decimal
    return_sign_consistency: str | None


def summarize_folds(metrics: tuple[PerformanceResults, ...]) -> WalkForwardSummary:
    rows = tuple(metrics)
    if not rows or any(not isinstance(row, PerformanceResults) for row in rows):
        raise ValueError("At least one valid fold performance result is required")
    for row in rows:
        for name in ("net_return", "maximum_drawdown", "expectancy", "profit_factor"):
            if getattr(row, name) is not None:
                require_decimal(getattr(row, name), name)
        for name in ("total_fees", "total_spread_cost", "total_slippage_cost"):
            require_decimal(getattr(row, name), name, nonnegative=True)
        if isinstance(row.trade_count, bool) or not isinstance(row.trade_count, int) or row.trade_count < 0:
            raise ValueError("Fold trade count must be a nonnegative integer")
    def defined(name):
        return tuple(getattr(row, name) for row in rows if getattr(row, name) is not None)
    returns, drawdowns = defined("net_return"), defined("maximum_drawdown")
    expectancy, factors = defined("expectancy"), defined("profit_factor")
    signs = {return_sign(value) for value in returns}
    return WalkForwardSummary(len(rows), sum(value > 0 for value in returns),
        sum(value < 0 for value in returns), sum(value == 0 for value in returns), len(rows)-len(returns),
        _mean(returns), _median(returns), min(returns) if returns else None, max(returns) if returns else None,
        _median(drawdowns), max(drawdowns) if drawdowns else None, len(drawdowns),
        sum(row.trade_count for row in rows), sum(row.trade_count == 0 for row in rows),
        _mean(expectancy), len(expectancy), _mean(factors), len(factors),
        exact_sum(row.total_fees for row in rows), exact_sum(row.total_spread_cost for row in rows),
        exact_sum(row.total_slippage_cost for row in rows),
        next(iter(signs)) if len(signs) == 1 else "MIXED" if signs else None)
