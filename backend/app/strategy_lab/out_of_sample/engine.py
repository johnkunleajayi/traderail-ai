"""Compose unchanged replay, execution and accounting on explicit period snapshots."""
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from app.strategies.contract import Strategy
from app.strategy_lab.accounting import ResearchResult, account_simulation
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.execution import simulate_execution
from app.strategy_lab.execution.config import identity
from app.strategy_lab.replay import replay_dataset
from app.strategy_lab.validation import TimeWindow
from .comparison import MetricComparison, compare_performance
from .plan import OutOfSamplePlan


@dataclass(frozen=True, slots=True)
class OutOfSampleResult:
    plan: OutOfSamplePlan
    in_sample: ResearchResult
    out_of_sample: ResearchResult
    comparison: tuple[MetricComparison, ...]
    validation_identity: str


def _evaluate_period(plan: OutOfSamplePlan, window: TimeWindow, strategy: Strategy, as_of: datetime):
    # Truncate at the period end: IS cannot access OOS and its pending fills cannot cross.
    # For OOS the explicitly declared IS prefix initializes analytics, never decisions/metrics.
    data = create_dataset(plan.dataset.context,
        tuple(row for row in plan.dataset.candles if row.open_time < window.end),
        warmup_start=plan.warmup.start, evaluation_start=window.start,
        evaluation_end=window.end, as_of=as_of)
    replay = replay_dataset(data, strategy, as_of=as_of, initial_state=plan.initial_state)
    if strategy.definition != plan.strategy_definition:
        raise ValueError("Strategy configuration changed during evaluation")
    execution = simulate_execution(data, replay, quantity=plan.quantity,
        cost_model=plan.execution_configuration.cost_model, as_of=as_of)
    if execution.configuration != plan.execution_configuration:
        raise ValueError("Execution configuration mismatch")
    return account_simulation(data, replay, execution, initial_capital=plan.initial_capital, as_of=as_of)


def validate_out_of_sample(plan: OutOfSamplePlan, strategy_factory: Callable[[], Strategy], *, as_of: datetime):
    """Factory supplies two fresh implementations of the exact predeclared configuration.

    No observations or results are passed to the factory; both instances are prepared before
    evaluation. This is not training, optimization or parameter selection.
    """
    if not isinstance(plan, OutOfSamplePlan) or not callable(strategy_factory):
        raise ValueError("An immutable validation plan and fresh strategy factory are required")
    # Revalidate even a frozen instance tampered with through low-level object APIs.
    checked = OutOfSamplePlan(plan.dataset, plan.warmup, plan.split, plan.strategy_definition,
        plan.execution_configuration, plan.quantity, plan.initial_capital,
        plan.accounting_configuration, plan.initial_state)
    if checked.plan_identity != plan.plan_identity:
        raise ValueError("Validation plan identity mismatch")
    create_dataset(plan.dataset.context, plan.dataset.candles,
        warmup_start=plan.dataset.warmup_start, evaluation_start=plan.dataset.evaluation_start,
        evaluation_end=plan.dataset.evaluation_end, as_of=as_of)
    ins_strategy, oos_strategy = strategy_factory(), strategy_factory()
    if ins_strategy is oos_strategy:
        raise ValueError("Independent periods require fresh strategy instances")
    for strategy in (ins_strategy, oos_strategy):
        if not isinstance(strategy, Strategy) or strategy.definition != plan.strategy_definition:
            raise ValueError("Strategy does not match the configuration frozen in the plan")
    ins = _evaluate_period(plan, plan.split.in_sample, ins_strategy, as_of)
    oos = _evaluate_period(plan, plan.split.out_of_sample, oos_strategy, as_of)
    comparison = compare_performance(ins.performance, oos.performance)
    result_identity = identity("strategy-lab.oos-result/v1", {
        "plan": plan.plan_identity, "in_sample": ins.result_identity,
        "out_of_sample": oos.result_identity, "comparison": [asdict(row) for row in comparison]})
    return OutOfSampleResult(plan, ins, oos, comparison, result_identity)
