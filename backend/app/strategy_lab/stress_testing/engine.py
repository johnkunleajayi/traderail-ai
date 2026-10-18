"""Unranked execution stress evidence through unchanged OOS and accounting engines."""
from dataclasses import dataclass, asdict
from decimal import Decimal
from app.strategies.adaptive_trend import AdaptiveTrendStrategy, AdaptiveTrendConfiguration
from app.strategy_lab.accounting.types import PerformanceResults
from app.strategy_lab.execution.config import identity
from app.strategy_lab.numeric import difference, exact_sum
from app.strategy_lab.out_of_sample import OutOfSamplePlan, OutOfSampleResult, compare_performance, validate_out_of_sample
from app.strategy_lab.walk_forward import WalkForwardResult, validate_walk_forward
from app.strategy_lab.walk_forward.summary import return_sign
from .plan import StressPlan, StressScenario, configured_validation


@dataclass(frozen=True, slots=True)
class StressComparison:
    fold_index: int
    metric_changes: tuple[tuple[str, Decimal | int | None], ...]
    total_cost_change: Decimal
    return_sign_changed: bool | None


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: StressScenario
    validation: OutOfSampleResult | WalkForwardResult
    oos_metrics: tuple[PerformanceResults, ...]
    differences: tuple[StressComparison, ...]
    result_identity: str


@dataclass(frozen=True, slots=True)
class StressResult:
    plan: StressPlan
    baseline: ScenarioResult
    scenarios: tuple[ScenarioResult, ...]
    stress_identity: str


def _metrics(result):
    return (result.out_of_sample.performance,) if isinstance(result, OutOfSampleResult) else tuple(
        row.validation.out_of_sample.performance for row in result.folds)


def _run(plan, scenario, as_of):
    validation = configured_validation(plan.validation, scenario.costs)
    definition = validation.strategy_definition if isinstance(validation, OutOfSamplePlan) else validation.folds[0].strategy_definition
    config = AdaptiveTrendConfiguration(**dict(definition.parameters))
    factory = lambda: AdaptiveTrendStrategy(config)
    # Exceptions (including insufficient capital) fail explicitly; never resize or suppress failures.
    if isinstance(validation, OutOfSamplePlan):
        return validate_out_of_sample(validation, factory, as_of=as_of)
    return validate_walk_forward(validation, factory, as_of=as_of)


def _scenario_result(scenario, validation, baseline_metrics):
    metrics = _metrics(validation)
    if len(metrics) != len(baseline_metrics):
        raise ValueError("Stress fold count differs from baseline")
    comparisons = []
    for index, (baseline, stressed) in enumerate(zip(baseline_metrics, metrics)):
        changes = tuple((row.metric, row.oos_minus_is) for row in compare_performance(baseline, stressed))
        def total(row):
            return exact_sum((row.total_fees, row.total_spread_cost, row.total_slippage_cost))
        signs = return_sign(baseline.net_return), return_sign(stressed.net_return)
        comparisons.append(StressComparison(index, changes, difference(total(stressed), total(baseline)),
            None if None in signs else signs[0] != signs[1]))
    result_id = identity("strategy-lab.stress-scenario-result/v1", {
        "scenario": scenario.scenario_identity, "validation": validation.validation_identity,
        "comparisons": tuple(asdict(row) for row in comparisons)})
    return ScenarioResult(scenario, validation, metrics, tuple(comparisons), result_id)


def evaluate_stress(plan: StressPlan, *, as_of):
    if not isinstance(plan, StressPlan):
        raise ValueError("An explicit immutable stress plan is required")
    if StressPlan(plan.validation, plan.scenarios).plan_identity != plan.plan_identity:
        raise ValueError("Stress plan identity mismatch")
    base = next(row for row in plan.scenarios if row.code == "BASELINE")
    result = _run(plan, base, as_of)
    baseline = _scenario_result(base, result, _metrics(result))
    scenarios = tuple(baseline if row.code == "BASELINE" else
        _scenario_result(row, _run(plan, row, as_of), baseline.oos_metrics) for row in plan.scenarios)
    result_id = identity("strategy-lab.stress-result/v1", {"plan": plan.plan_identity,
        "scenarios": tuple(row.result_identity for row in scenarios)})
    return StressResult(plan, baseline, scenarios, result_id)
