"""Centralized development hypotheses, never empirical profitability claims."""
from dataclasses import dataclass, field, fields
from decimal import Decimal
from enum import Enum
from app.strategy_lab.execution.config import identity
from app.strategy_lab.numeric import require_decimal


class BenchmarkTreatment(str, Enum):
    INFORMATIONAL = "INFORMATIONAL"
    MANDATORY = "MANDATORY"


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    version: str = "1.0.0"
    minimum_oos_completed_trades: int = 30
    minimum_walk_forward_folds: int = 3
    minimum_active_folds: int = 3
    minimum_non_losing_fold_proportion: Decimal = Decimal(".67")
    maximum_oos_drawdown: Decimal = Decimal(".20")
    maximum_worst_fold_drawdown: Decimal = Decimal(".25")
    minimum_oos_expectancy: Decimal = Decimal("0")
    minimum_oos_profit_factor: Decimal = Decimal("1.10")
    minimum_stress_scenarios: int = 4  # Includes BASELINE and explicitly recorded failed attempts.
    maximum_stress_return_degradation: Decimal = Decimal(".03")
    allow_stress_return_sign_flip: bool = False
    minimum_stress_completed_trades: int = 10
    minimum_stress_trade_retention: Decimal = Decimal(".50")
    require_sensitivity_stability: bool = True
    minimum_sensitivity_variants: int = 2  # Excludes baseline.
    maximum_sensitivity_return_degradation: Decimal = Decimal(".02")
    allow_sensitivity_return_sign_flip: bool = False
    minimum_variant_completed_trades: int = 10
    minimum_variant_trade_retention: Decimal = Decimal(".50")
    require_cash_comparison: bool = True
    require_buy_and_hold_comparison: bool = True
    cash_treatment: BenchmarkTreatment = BenchmarkTreatment.INFORMATIONAL
    buy_and_hold_treatment: BenchmarkTreatment = BenchmarkTreatment.INFORMATIONAL
    minimum_benchmark_excess_return: Decimal = Decimal("0")
    benchmark_scope: str = "PRIMARY_OOS"
    fold_consistency_basis: str = "ACTIVE_FOLDS"
    policy_identity: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("Explicit policy version required")
        for name in ("minimum_oos_completed_trades", "minimum_walk_forward_folds", "minimum_active_folds",
            "minimum_stress_scenarios", "minimum_stress_completed_trades", "minimum_sensitivity_variants",
            "minimum_variant_completed_trades"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.minimum_stress_scenarios < 2:
            raise ValueError("Stress evidence requires BASELINE and at least one alternative")
        for name in ("minimum_non_losing_fold_proportion", "maximum_oos_drawdown", "maximum_worst_fold_drawdown",
            "maximum_stress_return_degradation", "minimum_stress_trade_retention",
            "maximum_sensitivity_return_degradation", "minimum_variant_trade_retention"):
            value = require_decimal(getattr(self, name), name, nonnegative=True)
            if value > 1:
                raise ValueError(f"{name} must be a fraction between zero and one")
        require_decimal(self.minimum_oos_expectancy, "minimum_oos_expectancy")
        require_decimal(self.minimum_oos_profit_factor, "minimum_oos_profit_factor", nonnegative=True)
        require_decimal(self.minimum_benchmark_excess_return, "minimum_benchmark_excess_return")
        for name in ("allow_stress_return_sign_flip", "require_sensitivity_stability", "allow_sensitivity_return_sign_flip",
            "require_cash_comparison", "require_buy_and_hold_comparison"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if not isinstance(self.cash_treatment, BenchmarkTreatment) or not isinstance(self.buy_and_hold_treatment, BenchmarkTreatment):
            raise ValueError("Typed benchmark treatment required")
        if self.benchmark_scope != "PRIMARY_OOS" or self.fold_consistency_basis != "ACTIVE_FOLDS":
            raise ValueError("Unsupported policy scope/convention")
        object.__setattr__(self, "policy_identity", identity("strategy-lab.validation-policy/v1", self.configuration()))

    def configuration(self):
        return {item.name: getattr(self, item.name) for item in fields(self) if item.name != "policy_identity"}


# All defaults are research-policy hypotheses requiring empirical review, not tuned to stored data.
DEFAULT_RESEARCH_POLICY = ValidationPolicy()
