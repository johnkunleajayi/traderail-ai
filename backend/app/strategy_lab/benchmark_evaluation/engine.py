"""Passive OOS comparisons using shared pricing and 9A metric definitions."""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from decimal import Decimal, localcontext, ROUND_DOWN
from app.strategies.decisions import DecisionType
from app.strategy_lab.benchmarks import BenchmarkConfiguration, BenchmarkKind
from app.strategy_lab.costs import CostBreakdown
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.execution.config import identity
from app.strategy_lab.execution.pricing import price_fill
from app.strategy_lab.numeric import ZERO, ONE, exact_sum, exact_product, difference
from app.strategy_lab import metrics
from app.strategy_lab.out_of_sample import OutOfSamplePlan, OutOfSampleResult
from app.strategy_lab.walk_forward import WalkForwardResult
from app.market_intelligence.types import DatasetContext
from app.strategy_lab.validation import TimeWindow
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration

BENCHMARK_EVALUATION_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class BenchmarkObservation:
    timestamp: datetime
    close: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    kind: BenchmarkKind
    dataset_identity: str
    window_identity: str
    canonical_symbol: str
    quote_currency: str
    initial_capital: Decimal
    ending_equity: Decimal
    total_return: Decimal | None
    maximum_drawdown: Decimal | None
    exposure_percentage: Decimal | None
    turnover: Decimal | None
    costs: CostBreakdown
    quantity: Decimal
    residual_cash: Decimal
    entry_time: datetime | None
    reference_open: Decimal | None
    fill_price: Decimal | None
    observations: tuple[BenchmarkObservation, ...]
    result_identity: str
    context: DatasetContext
    window: TimeWindow
    execution_configuration: HistoricalExecutionConfiguration
    engine_version: str = BENCHMARK_EVALUATION_VERSION


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    fold_index: int
    strategy_result_identity: str
    benchmark: BenchmarkResult
    differences: tuple[tuple[str, Decimal | None], ...]
    comparison_identity: str


def _benchmark(plan, definition, *, as_of):
    # Revalidate the frozen plan/snapshot; never fetch or repair history.
    OutOfSamplePlan(plan.dataset, plan.warmup, plan.split, plan.strategy_definition,
        plan.execution_configuration, plan.quantity, plan.initial_capital,
        plan.accounting_configuration, plan.initial_state)
    data = plan.dataset
    create_dataset(data.context, data.candles, warmup_start=data.warmup_start,
        evaluation_start=data.evaluation_start, evaluation_end=data.evaluation_end, as_of=as_of)
    window = plan.split.out_of_sample
    rows = tuple(row for row in data.candles if window.start <= row.open_time < window.end)
    if not rows:
        raise ValueError("No completed benchmark observations in the declared OOS window")
    capital, config = plan.initial_capital, plan.execution_configuration
    quantity, cash, costs = ZERO, capital, CostBreakdown()
    entry_time = reference = fill_price = None
    entry_index = None
    if definition.kind is BenchmarkKind.BUY_AND_HOLD:
        # The passive allocation is declared before the window, not decided at its first close.
        entry_index = config.delay_candles
        if entry_index >= len(rows):
            raise ValueError("Insufficient OOS history for the declared passive entry delay")
        reference = rows[entry_index].open
        unit = price_fill(reference, DecisionType.ENTER_LONG, ONE, config.cost_model)
        unit_budget = exact_sum((unit.fill_price, unit.fee_amount))
        # Fee-inclusive allocation, rounded DOWN only at the research sizing boundary.
        # Explicit 50 significant-digit quantity convention; never borrow rounding dust.
        with localcontext() as context:
            context.prec, context.rounding = 50, ROUND_DOWN
            quantity = capital / unit_budget
        fill = price_fill(reference, DecisionType.ENTER_LONG, quantity, config.cost_model)
        cash = difference(capital, exact_sum((fill.fill_notional, fill.fee_amount)))
        if cash < ZERO:
            raise ValueError("Passive allocation exceeds unleveraged capital")
        costs = CostBreakdown(fill.fee_amount, fill.spread_cost, fill.slippage_cost)
        fill_price, entry_time = fill.fill_price, rows[entry_index].open_time
    observations = tuple(BenchmarkObservation(row.close_time, row.close,
        capital if entry_index is None or index < entry_index else
        exact_sum((cash, exact_product(quantity, row.close)))) for index, row in enumerate(rows))
    ending = observations[-1].equity
    exposure = timedelta(0) if entry_time is None else window.end - entry_time
    total_return = metrics.total_return(capital, ending)
    drawdown = metrics.maximum_drawdown((capital, *(row.equity for row in observations)))
    turnover = metrics.turnover(() if fill_price is None else (exact_product(quantity, fill_price),), capital)
    exposure_value = metrics.exposure_percentage(exposure, window.end-window.start)
    window_id = identity("strategy-lab.benchmark-window/v1", {
        "dataset": data.dataset_fingerprint, "context": {"instrument_id": str(data.context.instrument_id), "canonical_symbol": data.context.canonical_symbol, "provider": data.context.provider, "timeframe": data.context.timeframe}, "window": window.configuration()})
    payload = {"version": BENCHMARK_EVALUATION_VERSION, "definition": definition.configuration(),
        "window": window_id, "capital": capital, "execution": config.configuration(),
        "pricing": "predeclared_first_oos_open_plus_whole_candle_delay_entry_only_final_close_mark",
        "quantity_convention": "fee_inclusive_budget_50_significant_digits_ROUND_DOWN",
        "quantity": quantity, "cash": cash, "entry_time": entry_time,
        "reference_open": reference, "fill_price": fill_price, "costs": asdict(costs),
        "observations": tuple(asdict(row) for row in observations)}
    return BenchmarkResult(definition.kind, data.dataset_fingerprint, window_id,
        data.context.canonical_symbol, data.context.canonical_symbol.split("/")[1], capital,
        ending, total_return, drawdown, exposure_value, turnover, costs, quantity, cash,
        entry_time, reference, fill_price, observations, identity("strategy-lab.benchmark-result/v1", payload), data.context, window, config)


def evaluate_benchmarks(validation, configuration: BenchmarkConfiguration, *, as_of):
    if not isinstance(configuration, BenchmarkConfiguration):
        raise ValueError("Explicit benchmark definitions are required")
    configuration = BenchmarkConfiguration(configuration.definitions)
    if isinstance(validation, OutOfSampleResult):
        periods = ((validation.plan, validation.out_of_sample),)
    elif isinstance(validation, WalkForwardResult):
        periods = tuple((row.validation.plan, row.validation.out_of_sample) for row in validation.folds)
    else:
        raise ValueError("Completed paired or walk-forward validation is required")
    comparisons = []
    for index, (plan, strategy) in enumerate(periods):
        window = plan.split.out_of_sample
        if (strategy.context != plan.dataset.context or strategy.initial_capital != plan.initial_capital
            or strategy.replay.evaluation_start != window.start or strategy.replay.evaluation_end != window.end
            or strategy.replay.strategy_definition != plan.strategy_definition
            or strategy.execution.configuration != plan.execution_configuration
            or strategy.accounting_configuration != plan.accounting_configuration):
            raise ValueError("Strategy and benchmark must share canonical context, capital and OOS window")
        expected = create_dataset(plan.dataset.context, plan.dataset.candles,
            warmup_start=plan.dataset.warmup_start, evaluation_start=window.start,
            evaluation_end=window.end, as_of=as_of)
        if strategy.dataset_fingerprint != expected.dataset_fingerprint:
            raise ValueError("Strategy and benchmark frozen candle data differ")
        for definition in configuration.definitions:
            benchmark = _benchmark(plan, definition, as_of=as_of)
            performance = strategy.performance
            pairs = (("total_return", performance.total_return, benchmark.total_return),
                ("maximum_drawdown", performance.maximum_drawdown, benchmark.maximum_drawdown),
                ("exposure_percentage", performance.exposure_percentage, benchmark.exposure_percentage),
                ("turnover", performance.turnover, benchmark.turnover),
                ("total_cost", strategy.incurred_costs.total, benchmark.costs.total))
            differences = tuple((name, None if left is None or right is None else difference(left, right))
                for name, left, right in pairs)
            comparison_id = identity("strategy-lab.benchmark-comparison/v1", {
                "fold": index, "strategy": strategy.result_identity,
                "benchmark": benchmark.result_identity, "differences": differences})
            comparisons.append(BenchmarkComparison(index, strategy.result_identity, benchmark, differences, comparison_id))
    return tuple(comparisons)
