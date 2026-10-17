"""Explicit research assumptions and cost arithmetic, without execution simulation."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from .numeric import ZERO, ONE, duration_microseconds, exact_product, exact_sum, require_decimal

COST_MODEL_VERSION = "1.0.0"


@dataclass(frozen=True)
class CostBreakdown:
    fees: Decimal = ZERO
    spread_cost: Decimal = ZERO
    slippage_cost: Decimal = ZERO

    def __post_init__(self):
        for name in ("fees", "spread_cost", "slippage_cost"):
            require_decimal(getattr(self, name), name, nonnegative=True)

    @property
    def total(self) -> Decimal:
        return exact_sum((self.fees, self.spread_cost, self.slippage_cost))


@dataclass(frozen=True)
class ResearchCostModel:
    trading_fee_rate: Decimal
    spread_rate: Decimal
    slippage_rate: Decimal
    execution_delay: timedelta = timedelta(0)

    def __post_init__(self):
        for name in ("trading_fee_rate", "spread_rate", "slippage_rate"):
            value = require_decimal(getattr(self, name), name, nonnegative=True)
            if value > ONE:
                raise ValueError(f"{name} must be a fraction between zero and one")
        duration_microseconds(self.execution_delay, "execution_delay")

    def costs_for_notional(self, notional: Decimal) -> CostBreakdown:
        """Per-leg cost arithmetic on a caller-supplied unsigned notional; no fills."""
        require_decimal(notional, "notional", nonnegative=True)
        return CostBreakdown(exact_product(notional, self.trading_fee_rate),
            exact_product(notional, exact_product(self.spread_rate, Decimal("0.5"))),
            exact_product(notional, self.slippage_rate))

    def configuration(self) -> dict:
        """Detached data compatible with Part 1's canonical configuration snapshots."""
        return {"cost_model_version": COST_MODEL_VERSION,
                "trading_fee_rate": self.trading_fee_rate, "spread_rate": self.spread_rate,
                "spread_convention": "full_bid_ask_spread_half_per_leg",
                "slippage_rate": self.slippage_rate,
                "execution_delay_microseconds": duration_microseconds(self.execution_delay, "execution_delay")}
