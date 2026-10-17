"""Pure measurement primitives over caller-supplied equity and realized outcomes."""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from .costs import CostBreakdown
from .numeric import ZERO, difference, duration_microseconds, exact_product, exact_sum, ratio, require_decimal


@dataclass(frozen=True)
class RealizedTrade:
    """An accounting observation, not an order, fill, strategy or simulator."""
    gross_pnl: Decimal
    costs: CostBreakdown = CostBreakdown()

    def __post_init__(self):
        require_decimal(self.gross_pnl, "gross_pnl")
        if not isinstance(self.costs, CostBreakdown):
            raise ValueError("costs must be a CostBreakdown")

    @property
    def net_pnl(self) -> Decimal:
        return difference(self.gross_pnl, self.costs.total)


@dataclass(frozen=True)
class RealizedPnlTotals:
    gross_pnl: Decimal
    fees: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    net_pnl: Decimal


def _trades(trades: Sequence[RealizedTrade]) -> tuple[RealizedTrade, ...]:
    rows = tuple(trades)
    if any(not isinstance(row, RealizedTrade) for row in rows):
        raise ValueError("Expected realized trade observations")
    return rows


def _capital(value: Decimal) -> Decimal:
    return require_decimal(value, "starting_capital", nonnegative=True)


def total_return(starting_capital: Decimal, ending_equity: Decimal) -> Decimal | None:
    """Simple equity return, without deposits/withdrawals or flow adjustment."""
    _capital(starting_capital)
    require_decimal(ending_equity, "ending_equity", nonnegative=True)
    return ratio(difference(ending_equity, starting_capital), starting_capital)


def realized_pnl_aggregation(trades: Sequence[RealizedTrade]) -> RealizedPnlTotals:
    rows = _trades(trades)
    gross = exact_sum(row.gross_pnl for row in rows)
    fees = exact_sum(row.costs.fees for row in rows)
    spread = exact_sum(row.costs.spread_cost for row in rows)
    slippage = exact_sum(row.costs.slippage_cost for row in rows)
    return RealizedPnlTotals(gross, fees, spread, slippage,
                            difference(gross, exact_sum((fees, spread, slippage))))


def gross_return(starting_capital: Decimal, trades: Sequence[RealizedTrade]) -> Decimal | None:
    return ratio(realized_pnl_aggregation(trades).gross_pnl, _capital(starting_capital))


def net_return(starting_capital: Decimal, trades: Sequence[RealizedTrade]) -> Decimal | None:
    return ratio(realized_pnl_aggregation(trades).net_pnl, _capital(starting_capital))


def drawdown_series(equity: Sequence[Decimal]) -> tuple[Decimal | None, ...]:
    """Nonnegative fractional declines from each running peak; no synthetic baseline."""
    peak = ZERO
    result = []
    for value in equity:
        require_decimal(value, "equity", nonnegative=True)
        peak = max(peak, value)
        result.append(ratio(difference(peak, value), peak))
    return tuple(result)


def maximum_drawdown(equity: Sequence[Decimal]) -> Decimal | None:
    available = [value for value in drawdown_series(equity) if value is not None]
    return max(available) if available else None


def trade_count(trades: Sequence[RealizedTrade]) -> int:
    return len(_trades(trades))


def win_rate(trades: Sequence[RealizedTrade]) -> Decimal | None:
    rows = _trades(trades)
    return ratio(Decimal(sum(row.net_pnl > ZERO for row in rows)), Decimal(len(rows)))


def loss_rate(trades: Sequence[RealizedTrade]) -> Decimal | None:
    rows = _trades(trades)
    return ratio(Decimal(sum(row.net_pnl < ZERO for row in rows)), Decimal(len(rows)))


def profit_factor(trades: Sequence[RealizedTrade]) -> Decimal | None:
    rows = _trades(trades)
    wins = exact_sum(row.net_pnl for row in rows if row.net_pnl > ZERO)
    losses = exact_sum(row.net_pnl.copy_abs() for row in rows if row.net_pnl < ZERO)
    return ratio(wins, losses)


def average_win(trades: Sequence[RealizedTrade]) -> Decimal | None:
    wins = [row.net_pnl for row in _trades(trades) if row.net_pnl > ZERO]
    return ratio(exact_sum(wins), Decimal(len(wins)))


def average_loss(trades: Sequence[RealizedTrade]) -> Decimal | None:
    """Mean absolute magnitude of strictly negative net outcomes."""
    losses = [row.net_pnl.copy_abs() for row in _trades(trades) if row.net_pnl < ZERO]
    return ratio(exact_sum(losses), Decimal(len(losses)))


def expectancy(trades: Sequence[RealizedTrade]) -> Decimal | None:
    rows = _trades(trades)
    return ratio(exact_sum(row.net_pnl for row in rows), Decimal(len(rows)))


def exposure_percentage(exposed_duration: timedelta, evaluation_duration: timedelta) -> Decimal | None:
    exposed = duration_microseconds(exposed_duration, "exposed_duration")
    duration = duration_microseconds(evaluation_duration, "evaluation_duration")
    if exposed > duration:
        raise ValueError("Exposed duration cannot exceed evaluation duration")
    return ratio(exact_product(Decimal(exposed), Decimal("100")), Decimal(duration))


def turnover(executed_notionals: Sequence[Decimal], starting_capital: Decimal) -> Decimal | None:
    """Gross unsigned traded notional / initial capital; counts each leg, no halving."""
    capital = _capital(starting_capital)
    rows = tuple(require_decimal(value, "notional", nonnegative=True) for value in executed_notionals)
    return ratio(exact_sum(rows), capital)


def total_fees(trades: Sequence[RealizedTrade]) -> Decimal:
    return realized_pnl_aggregation(trades).fees


def total_spread_cost(trades: Sequence[RealizedTrade]) -> Decimal:
    return realized_pnl_aggregation(trades).spread_cost


def total_slippage_cost(trades: Sequence[RealizedTrade]) -> Decimal:
    return realized_pnl_aggregation(trades).slippage_cost
