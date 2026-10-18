"""Evaluate explicit alternatives identically; retain all results without selection."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from app.strategies.adaptive_trend import AdaptiveTrendConfiguration, AdaptiveTrendStrategy
from app.strategy_lab.accounting.types import PerformanceResults
from app.strategy_lab.execution.config import identity
from app.strategy_lab.out_of_sample import OutOfSamplePlan, OutOfSampleResult, compare_performance, validate_out_of_sample
from app.strategy_lab.walk_forward import WalkForwardPlan, WalkForwardResult, validate_walk_forward
from app.strategy_lab.walk_forward.summary import return_sign
from .plan import SensitivityPlan, changed_fields


@dataclass(frozen=True, slots=True)
class SensitivityMetricDifference:
    metric: str
    baseline: Decimal | int | None
    variant: Decimal | int | None
    variant_minus_baseline: Decimal | int | None


@dataclass(frozen=True, slots=True)
class SensitivityComparison:
    fold_index: int
    metrics: tuple[SensitivityMetricDifference, ...]
    return_sign_changed: bool | None


@dataclass(frozen=True, slots=True)
class VariantResult:
    configuration: AdaptiveTrendConfiguration
    configuration_identity: str
    changed_fields: tuple[str, ...]
    validation: OutOfSampleResult | WalkForwardResult
    oos_metrics: tuple[PerformanceResults, ...]
    differences: tuple[SensitivityComparison, ...]


@dataclass(frozen=True, slots=True)
class SensitivityResult:
    plan: SensitivityPlan
    baseline: VariantResult
    variants: tuple[VariantResult, ...]
    sensitivity_identity: str


def _configured_validation(validation, definition):
    if isinstance(validation, OutOfSamplePlan):
        return replace(validation, strategy_definition=definition)
    return replace(validation, folds=tuple(replace(fold, strategy_definition=definition) for fold in validation.folds))


def _run(plan, configuration, as_of):
    definition = AdaptiveTrendStrategy(configuration).definition
    validation = _configured_validation(plan.validation, definition)
    factory = lambda: AdaptiveTrendStrategy(configuration)
    if isinstance(validation, OutOfSamplePlan):
        result = validate_out_of_sample(validation, factory, as_of=as_of)
        metrics = (result.out_of_sample.performance,)
    else:
        result = validate_walk_forward(validation, factory, as_of=as_of)
        metrics = tuple(fold.validation.out_of_sample.performance for fold in result.folds)
    return result, metrics


def _variant(plan, configuration, result, metrics, baseline_metrics):
    differences = []
    if len(metrics) != len(baseline_metrics):
        raise ValueError("Variant fold count differs from baseline")
    for index, (baseline, variant) in enumerate(zip(baseline_metrics, metrics)):
        signs = (return_sign(baseline.net_return), return_sign(variant.net_return))
        # Reuse the existing arithmetic but expose accurate baseline/variant labels.
        metric_differences = tuple(SensitivityMetricDifference(row.metric, row.in_sample, row.out_of_sample,
            row.oos_minus_is) for row in compare_performance(baseline, variant))
        differences.append(SensitivityComparison(index, metric_differences,
            None if None in signs else signs[0] != signs[1]))
    return VariantResult(configuration, AdaptiveTrendStrategy(configuration).definition.configuration_identity,
        changed_fields(plan.baseline, configuration), result, metrics, tuple(differences))


def evaluate_sensitivity(plan: SensitivityPlan, *, as_of: datetime):
    if not isinstance(plan, SensitivityPlan):
        raise ValueError("An immutable declared sensitivity plan is required")
    rebuilt = SensitivityPlan(plan.validation, plan.baseline, plan.configurations, plan.ofat)
    if rebuilt.plan_identity != plan.plan_identity:
        raise ValueError("Sensitivity plan identity mismatch")
    base_result, base_metrics = _run(plan, plan.baseline, as_of)
    baseline = _variant(plan, plan.baseline, base_result, base_metrics, base_metrics)
    variants = []
    for configuration in plan.configurations:
        if AdaptiveTrendStrategy(configuration).definition.configuration_identity == baseline.configuration_identity:
            continue  # Baseline already evaluated once, never omitted from output.
        result, metrics = _run(plan, configuration, as_of)
        variants.append(_variant(plan, configuration, result, metrics, base_metrics))
    result_id = identity("strategy-lab.sensitivity-result/v1", {
        "plan": plan.plan_identity, "baseline_validation": baseline.validation.validation_identity,
        "variants": tuple({"configuration": row.configuration_identity,
            "validation": row.validation.validation_identity,
            "differences": tuple(asdict(item) for item in row.differences)} for row in variants)})
    return SensitivityResult(plan, baseline, tuple(variants), result_id)
