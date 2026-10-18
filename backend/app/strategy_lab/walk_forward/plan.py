"""Frozen disjoint fold plans; no training, rolling reuse or parameter selection."""
from dataclasses import dataclass, field
from app.strategy_lab.dataset import ResearchDataset, create_dataset
from app.strategy_lab.execution.config import identity
from app.strategy_lab.out_of_sample import OutOfSamplePlan
from app.strategy_lab.validation import ValidationKind, ValidationPlan

WALK_FORWARD_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class WalkForwardPlan:
    dataset: ResearchDataset
    folds: tuple[OutOfSamplePlan, ...]
    plan_identity: str = field(init=False)

    def __post_init__(self):
        if (not isinstance(self.dataset, ResearchDataset) or not isinstance(self.dataset.candles, tuple)
            or not self.dataset.candles):
            raise ValueError("A frozen parent dataset is required")
        if not isinstance(self.folds, (tuple, list)) or not self.folds:
            raise ValueError("At least one explicit fold is required")
        folds = tuple(self.folds)
        if any(not isinstance(fold, OutOfSamplePlan) for fold in folds):
            raise ValueError("Each fold must be an immutable paired OOS plan")
        object.__setattr__(self, "folds", folds)
        data = self.dataset
        checked = create_dataset(data.context, data.candles, warmup_start=data.warmup_start,
            evaluation_start=data.evaluation_start, evaluation_end=data.evaluation_end,
            as_of=max(data.evaluation_end, *(row.retrieved_at for row in data.candles)))
        if checked.dataset_fingerprint != data.dataset_fingerprint:
            raise ValueError("Parent dataset fingerprint mismatch")
        for index, fold in enumerate(folds):
            # Reuse all Part 1 validation and reject manually forged plan identities.
            rebuilt = OutOfSamplePlan(fold.dataset, fold.warmup, fold.split, fold.strategy_definition,
                fold.execution_configuration, fold.quantity, fold.initial_capital,
                fold.accounting_configuration, fold.initial_state)
            if rebuilt.plan_identity != fold.plan_identity:
                raise ValueError("Fold plan identity mismatch")
            if (fold.dataset.context != data.context or fold.warmup.start < data.warmup_start
                or fold.dataset.evaluation_end > data.evaluation_end):
                raise ValueError("Fold lies outside the frozen parent dataset/context")
            expected = tuple(row for row in data.candles
                if fold.warmup.start <= row.open_time < fold.dataset.evaluation_end)
            if expected != fold.dataset.candles:
                raise ValueError("Fold data must exactly match its declared parent slice")
            first = folds[0]
            assumptions = (fold.strategy_definition, fold.execution_configuration,
                fold.accounting_configuration, fold.quantity, fold.initial_capital, fold.initial_state)
            original = (first.strategy_definition, first.execution_configuration,
                first.accounting_configuration, first.quantity, first.initial_capital, first.initial_state)
            if assumptions != original:
                raise ValueError("All folds must use one unchanged strategy and research assumptions")
            if index and folds[index-1].dataset.evaluation_end > fold.warmup.start:
                raise ValueError("Whole fold windows must progress without overlapping history or OOS")
        # 9A requires two folds for WALK_FORWARD; a one-fold run uses its paired split.
        kind = ValidationKind.WALK_FORWARD if len(folds) > 1 else ValidationKind.OUT_OF_SAMPLE
        ValidationPlan(kind, tuple(fold.split for fold in folds)).validate_for(data)
        object.__setattr__(self, "plan_identity", identity("strategy-lab.walk-forward-plan/v1", self.configuration()))

    def configuration(self):
        return {"walk_forward_version": WALK_FORWARD_VERSION,
            "dataset_identity": self.dataset.dataset_fingerprint,
            "fold_policy": "disjoint_including_warmup_independent_CASH",
            "fold_plan_identities": tuple(fold.plan_identity for fold in self.folds)}
