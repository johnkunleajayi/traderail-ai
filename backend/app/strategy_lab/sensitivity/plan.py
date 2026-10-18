"""Caller-declared Adaptive Trend alternatives; no variant generator or optimizer."""
from dataclasses import dataclass, field, fields, replace
from app.strategies.adaptive_trend import AdaptiveTrendConfiguration, AdaptiveTrendStrategy
from app.strategy_lab.execution.config import identity
from app.strategy_lab.out_of_sample import OutOfSamplePlan
from app.strategy_lab.walk_forward import WalkForwardPlan

SENSITIVITY_VERSION = "1.0.0"


def changed_fields(baseline, variant):
    return tuple(sorted(item.name for item in fields(AdaptiveTrendConfiguration)
        if getattr(baseline, item.name) != getattr(variant, item.name)))


@dataclass(frozen=True, slots=True)
class SensitivityPlan:
    validation: OutOfSamplePlan | WalkForwardPlan
    baseline: AdaptiveTrendConfiguration
    configurations: tuple[AdaptiveTrendConfiguration, ...]
    ofat: bool = True
    plan_identity: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.validation, (OutOfSamplePlan, WalkForwardPlan)):
            raise ValueError("Explicit paired or walk-forward validation is required")
        if replace(self.validation).plan_identity != self.validation.plan_identity:
            raise ValueError("Validation plan identity mismatch")
        if not isinstance(self.baseline, AdaptiveTrendConfiguration) or not isinstance(self.ofat, bool):
            raise ValueError("A typed Adaptive Trend baseline and boolean OFAT mode are required")
        if not isinstance(self.configurations, (tuple, list)) or not self.configurations:
            raise ValueError("Configurations must explicitly include the baseline")
        baseline_definition = AdaptiveTrendStrategy(self.baseline).definition
        folds = self.validation.folds if isinstance(self.validation, WalkForwardPlan) else (self.validation,)
        if any(fold.strategy_definition != baseline_definition for fold in folds):
            raise ValueError("Baseline must match the exact strategy configuration frozen in validation")
        ids, configs = set(), []
        for config in self.configurations:
            if not isinstance(config, AdaptiveTrendConfiguration):
                raise ValueError("Each alternative must be a typed explicit configuration")
            config_id = AdaptiveTrendStrategy(config).definition.configuration_identity
            if config_id in ids:
                raise ValueError("Duplicate/equivalent variant configuration")
            ids.add(config_id)
            differences = changed_fields(self.baseline, config)
            if self.ofat and config_id != baseline_definition.configuration_identity and len(differences) != 1:
                raise ValueError("OFAT alternatives must change exactly one meaningful field")
            configs.append(config)
        if baseline_definition.configuration_identity not in ids:
            raise ValueError("Baseline configuration was omitted")
        # Identity ordering only: never sort, rank or select by profitability.
        configs.sort(key=lambda config: AdaptiveTrendStrategy(config).definition.configuration_identity)
        object.__setattr__(self, "configurations", tuple(configs))
        object.__setattr__(self, "plan_identity", identity("strategy-lab.sensitivity-plan/v1", {
            "sensitivity_version": SENSITIVITY_VERSION, "validation": self.validation.plan_identity,
            "baseline": baseline_definition.configuration_identity, "ofat": self.ofat,
            "configuration_identities": tuple(AdaptiveTrendStrategy(config).definition.configuration_identity for config in configs)}))
