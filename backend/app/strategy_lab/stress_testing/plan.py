"""Caller-declared execution assumptions only; no strategy variation or search."""
from dataclasses import dataclass, field, replace
from app.strategies.adaptive_trend import AdaptiveTrendStrategy, AdaptiveTrendConfiguration
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration, identity
from app.strategy_lab.out_of_sample import OutOfSamplePlan
from app.strategy_lab.walk_forward import WalkForwardPlan


@dataclass(frozen=True, slots=True)
class StressScenario:
    code: str
    costs: ResearchCostModel
    scenario_identity: str = field(init=False)

    def __post_init__(self):
        if (not isinstance(self.code, str) or not self.code or not self.code.isascii()
            or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in self.code)):
            raise ValueError("An explicit uppercase scenario code is required")
        if not isinstance(self.costs, ResearchCostModel):
            raise ValueError("Typed execution costs are required")
        checked = ResearchCostModel(self.costs.trading_fee_rate, self.costs.spread_rate,
            self.costs.slippage_rate, self.costs.execution_delay)
        object.__setattr__(self, "scenario_identity", identity("strategy-lab.stress-scenario/v1",
            {"code": self.code, "costs": checked.configuration()}))


def configured_validation(validation, costs):
    def configured(fold):
        return replace(fold, execution_configuration=HistoricalExecutionConfiguration(costs, fold.dataset.context.timeframe))
    return configured(validation) if isinstance(validation, OutOfSamplePlan) else replace(validation,
        folds=tuple(configured(fold) for fold in validation.folds))


@dataclass(frozen=True, slots=True)
class StressPlan:
    validation: OutOfSamplePlan | WalkForwardPlan
    scenarios: tuple[StressScenario, ...]
    plan_identity: str = field(init=False)

    def __post_init__(self):
        if isinstance(self.validation, OutOfSamplePlan):
            checked = replace(self.validation)
            folds = (checked,)
        elif isinstance(self.validation, WalkForwardPlan):
            checked = replace(self.validation)
            folds = checked.folds
        else:
            raise ValueError("A frozen paired or walk-forward plan is required")
        if checked.plan_identity != self.validation.plan_identity:
            raise ValueError("Validation plan identity mismatch")
        # Only the unchanged Adaptive Trend family is currently validated here.
        for fold in folds:
            config = AdaptiveTrendConfiguration(**dict(fold.strategy_definition.parameters))
            if AdaptiveTrendStrategy(config).definition != fold.strategy_definition:
                raise ValueError("Stress testing requires the frozen Adaptive Trend definition")
        if not isinstance(self.scenarios, (tuple, list)):
            raise ValueError("Explicit stress scenarios are required")
        scenarios = tuple(self.scenarios)
        if not scenarios or any(not isinstance(row, StressScenario) for row in scenarios):
            raise ValueError("Explicit typed stress scenarios are required")
        if len({row.code for row in scenarios}) != len(scenarios):
            raise ValueError("Duplicate scenario code")
        baseline = tuple(row for row in scenarios if row.code == "BASELINE")
        if len(baseline) != 1 or any(baseline[0].costs != fold.execution_configuration.cost_model for fold in folds):
            raise ValueError("Explicit BASELINE must match the frozen validation execution assumptions")
        for row in scenarios:
            if StressScenario(row.code, row.costs).scenario_identity != row.scenario_identity:
                raise ValueError("Scenario identity mismatch")
            configured_validation(self.validation, row.costs)  # validate whole-candle delay before any evaluation
        scenarios = tuple(sorted(scenarios, key=lambda row: row.code))
        object.__setattr__(self, "scenarios", scenarios)
        object.__setattr__(self, "plan_identity", identity("strategy-lab.stress-plan/v1", {
            "validation": checked.plan_identity, "scenarios": tuple(row.scenario_identity for row in scenarios)}))
