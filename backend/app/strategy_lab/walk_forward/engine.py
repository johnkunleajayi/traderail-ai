"""Independent folds composed exclusively through existing paired OOS validation."""
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from app.strategies.contract import Strategy
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.execution.config import identity
from app.strategy_lab.out_of_sample import OutOfSampleResult, validate_out_of_sample
from .plan import WalkForwardPlan
from .summary import WalkForwardSummary, summarize_folds


@dataclass(frozen=True, slots=True)
class FoldResult:
    index: int
    validation: OutOfSampleResult
    fold_identity: str


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    plan: WalkForwardPlan
    folds: tuple[FoldResult, ...]
    summary: WalkForwardSummary
    validation_identity: str


def validate_walk_forward(plan: WalkForwardPlan, strategy_factory: Callable[[], Strategy], *, as_of: datetime):
    if not isinstance(plan, WalkForwardPlan) or not callable(strategy_factory):
        raise ValueError("An immutable walk-forward plan and strategy factory are required")
    rebuilt = WalkForwardPlan(plan.dataset, plan.folds)
    if rebuilt.plan_identity != plan.plan_identity:
        raise ValueError("Walk-forward plan identity mismatch")
    data = plan.dataset
    create_dataset(data.context, data.candles, warmup_start=data.warmup_start,
        evaluation_start=data.evaluation_start, evaluation_end=data.evaluation_end, as_of=as_of)
    results, instances = [], []
    def fresh_strategy():
        instance = strategy_factory()
        if any(instance is previous for previous in instances):
            raise ValueError("Each period and fold requires a fresh strategy instance")
        instances.append(instance)
        return instance
    for index, fold in enumerate(plan.folds):
        result = validate_out_of_sample(fold, fresh_strategy, as_of=as_of)
        fold_id = identity("strategy-lab.walk-forward-fold/v1",
            {"index": index, "fold_plan": fold.plan_identity, "validation": result.validation_identity})
        results.append(FoldResult(index, result, fold_id))
    summary = summarize_folds(tuple(row.validation.out_of_sample.performance for row in results))
    result_id = identity("strategy-lab.walk-forward-result/v1", {"plan": plan.plan_identity,
        "folds": tuple(row.fold_identity for row in results), "summary": asdict(summary)})
    return WalkForwardResult(plan, tuple(results), summary, result_id)
