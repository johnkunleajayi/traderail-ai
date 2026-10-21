"""Explicit hypotheses from trusted backend callers, never exchange fee/lot claims."""
from dataclasses import dataclass, field
from decimal import Decimal

from app.strategy_lab.numeric import exact_product, exact_sum
from app.risk.canonical import decimal, require, seal
from app.risk.context import MarketRiskContext


@dataclass(frozen=True, slots=True)
class TradeRiskCosts:
    entry_fee_rate: Decimal
    exit_fee_rate: Decimal
    entry_full_spread_rate: Decimal
    exit_full_spread_rate: Decimal
    entry_slippage_rate: Decimal
    exit_slippage_rate: Decimal
    costs_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("entry_fee_rate", "exit_fee_rate", "entry_full_spread_rate", "exit_full_spread_rate",
                     "entry_slippage_rate", "exit_slippage_rate"):
            decimal(getattr(self, name), name, fraction=True)
        for side in ("entry", "exit"):
            adverse = exact_sum((exact_product(getattr(self, side + "_full_spread_rate"), Decimal("0.5")),
                                 getattr(self, side + "_slippage_rate")))
            if adverse >= 1:
                raise ValueError("Combined adverse spread/slippage must leave a positive modeled price")
        seal(self, "trade-risk-costs", "costs_identity")


@dataclass(frozen=True, slots=True)
class TradeSizeConstraints:
    # Optional *additional* declared caps. The 10A equity/exposure ceiling is mandatory.
    # None declares no additional rule, not an inferred exchange constraint.
    platform_max_notional: Decimal | None
    operating_max_notional: Decimal | None
    platform_max_quantity: Decimal | None
    operating_max_quantity: Decimal | None
    minimum_quantity: Decimal | None
    quantity_increment: Decimal | None
    constraints_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("platform_max_notional", "operating_max_notional", "platform_max_quantity",
                     "operating_max_quantity", "minimum_quantity", "quantity_increment"):
            value = getattr(self, name)
            if value is not None:
                decimal(value, name, positive=name in ("minimum_quantity", "quantity_increment"))
        for suffix in ("notional", "quantity"):
            hard = getattr(self, "platform_max_" + suffix)
            operating = getattr(self, "operating_max_" + suffix)
            if hard is not None and operating is not None and operating > hard:
                raise ValueError("Additional operating cap cannot exceed its platform hard cap")
        for maximum in (self.platform_max_quantity, self.operating_max_quantity):
            if maximum is not None and self.minimum_quantity is not None and maximum < self.minimum_quantity:
                raise ValueError("Minimum quantity exceeds a declared maximum")
        seal(self, "trade-size-constraints", "constraints_identity")


@dataclass(frozen=True, slots=True)
class SizingAssumptions:
    entry_reference_price: Decimal
    entry_context: MarketRiskContext
    requested_risk_fraction: Decimal
    costs: TradeRiskCosts
    constraints: TradeSizeConstraints
    proposed_quantity: Decimal | None = None
    assumptions_identity: str = field(init=False)

    def __post_init__(self):
        decimal(self.entry_reference_price, "entry_reference_price", positive=True)
        require(self.entry_context, MarketRiskContext, "entry_context")
        decimal(self.requested_risk_fraction, "requested_risk_fraction", fraction=True)
        require(self.costs, TradeRiskCosts, "costs")
        require(self.constraints, TradeSizeConstraints, "constraints")
        if self.proposed_quantity is not None:
            decimal(self.proposed_quantity, "proposed_quantity", positive=True)
        seal(self, "sizing-assumptions", "assumptions_identity")
