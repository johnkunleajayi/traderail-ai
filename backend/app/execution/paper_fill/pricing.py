"""Four-leg arithmetic only, delegated to the tested Strategy Lab price primitive."""
from dataclasses import dataclass, field
from decimal import Decimal
from app.risk.canonical import decimal, require
from app.risk.vocabulary import Direction
from app.strategies.decisions import DecisionType
from app.strategy_lab.execution.pricing import FillPricing, price_fill
from app.strategy_lab.numeric import exact_sum, difference
from ..canonical import execution_identity
from ..vocabulary import OrderSide
from .context import PaperFillAssumptions


@dataclass(frozen=True, slots=True)
class PaperLegCalculation:
    """PRICING ONLY. This is not authorization for an entry or an exit."""
    direction: Direction
    action: DecisionType
    reference_price: Decimal
    quantity: Decimal
    assumptions: PaperFillAssumptions
    pricing: FillPricing = field(init=False)
    net_cash_effect: Decimal = field(init=False)
    calculation_identity: str = field(init=False)

    def __post_init__(self):
        require(self.direction, Direction, "direction")
        require(self.action, DecisionType, "action")
        require(self.assumptions, PaperFillAssumptions, "assumptions")
        decimal(self.reference_price, "reference price", positive=True)
        decimal(self.quantity, "quantity", positive=True)
        allowed = ((DecisionType.ENTER_LONG, DecisionType.EXIT_LONG) if self.direction is Direction.LONG
                   else (DecisionType.ENTER_SHORT, DecisionType.EXIT_SHORT))
        if self.action not in allowed: raise ValueError("Unsupported direction/action combination")
        try:
            pricing = price_fill(self.reference_price, self.action, self.quantity, self.assumptions.cost_model)
            # Fee stays separate from execution price. This is a per-leg quote-currency
            # cashflow, NOT portfolio available cash or short-sale borrowing permission.
            cash = (exact_sum((pricing.fill_notional, pricing.fee_amount)).copy_negate()
                    if self.side is OrderSide.BUY else difference(pricing.fill_notional, pricing.fee_amount))
        except ArithmeticError:
            raise ValueError("Finite paper pricing arithmetic is unavailable") from None
        object.__setattr__(self, "pricing", pricing)
        object.__setattr__(self, "net_cash_effect", cash)
        object.__setattr__(self, "calculation_identity", execution_identity("paper-leg-calculation-1.0.0", {
            "scope":"PRICING_ONLY_NO_AUTHORITY", "direction":self.direction, "action":self.action,
            "quantity":self.quantity, "reference_price":self.reference_price,
            "assumptions":self.assumptions, "pricing":pricing, "net_cash_effect":cash}))

    @property
    def side(self):
        return OrderSide.BUY if self.action in (DecisionType.ENTER_LONG, DecisionType.EXIT_SHORT) else OrderSide.SELL
    @property
    def modeled_execution_price(self): return self.pricing.fill_price
    @property
    def spread_impact_per_unit(self): return self.pricing.spread_adjustment
    @property
    def slippage_impact_per_unit(self): return self.pricing.slippage_adjustment
    @property
    def reference_notional(self): return self.pricing.reference_notional
    @property
    def gross_notional(self): return self.pricing.fill_notional
    @property
    def spread_cost(self): return self.pricing.spread_cost
    @property
    def slippage_cost(self): return self.pricing.slippage_cost
    @property
    def fee(self): return self.pricing.fee_amount
