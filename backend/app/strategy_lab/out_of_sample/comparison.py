"""Descriptive OOS minus IS deltas; no scores, verdicts or promotion."""
from dataclasses import dataclass
from decimal import Decimal
from app.strategy_lab.accounting.types import PerformanceResults
from app.strategy_lab.numeric import difference

COMPARISON_METRICS = ("total_return", "net_return", "maximum_drawdown", "trade_count", "win_rate",
    "profit_factor", "expectancy", "exposure_percentage", "turnover", "total_fees",
    "total_spread_cost", "total_slippage_cost")


@dataclass(frozen=True, slots=True)
class MetricComparison:
    metric: str
    in_sample: Decimal | int | None
    out_of_sample: Decimal | int | None
    oos_minus_is: Decimal | int | None


def compare_performance(in_sample: PerformanceResults, out_of_sample: PerformanceResults):
    rows = []
    for name in COMPARISON_METRICS:
        ins, oos = getattr(in_sample, name), getattr(out_of_sample, name)
        delta = None if ins is None or oos is None else (
            oos - ins if isinstance(ins, int) and isinstance(oos, int) else difference(oos, ins))
        rows.append(MetricComparison(name, ins, oos, delta))
    return tuple(rows)
