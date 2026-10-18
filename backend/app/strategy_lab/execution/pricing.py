"""Exact adverse historical reference pricing; no favorable intrabar selection."""
from dataclasses import dataclass
from decimal import Decimal
from app.strategies.decisions import DecisionType
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.numeric import require_decimal, exact_product, exact_sum


@dataclass(frozen=True, slots=True)
class FillPricing:
    reference_open: Decimal
    spread_adjustment: Decimal
    slippage_adjustment: Decimal
    fill_price: Decimal
    reference_notional: Decimal
    fill_notional: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    fee_rate: Decimal
    fee_amount: Decimal


def price_fill(reference_open: Decimal, action: DecisionType, quantity: Decimal,
               model: ResearchCostModel) -> FillPricing:
    require_decimal(reference_open, "reference_open")
    require_decimal(quantity, "quantity")
    if reference_open <= 0 or quantity <= 0:
        raise ValueError("Reference open and research quantity must be positive")
    if not isinstance(model, ResearchCostModel) or not isinstance(action, DecisionType):
        raise ValueError("Typed costs and an actionable strategy decision are required")
    if action is DecisionType.NO_ACTION:
        raise ValueError("NO_ACTION cannot produce a fill")
    # Rate validity is checked even for independently invoked pricing.
    ResearchCostModel(model.trading_fee_rate, model.spread_rate, model.slippage_rate, model.execution_delay)
    spread = exact_product(reference_open, exact_product(model.spread_rate, Decimal("0.5")))
    slippage = exact_product(reference_open, model.slippage_rate)
    upward = action in (DecisionType.ENTER_LONG, DecisionType.EXIT_SHORT)
    signed_spread = spread if upward else spread.copy_negate()
    signed_slippage = slippage if upward else slippage.copy_negate()
    price = exact_sum((reference_open, signed_spread, signed_slippage))
    if price <= 0:
        raise ValueError("Adverse costs result in a non-positive simulated fill price")
    reference_notional = exact_product(quantity, reference_open)
    fill_notional = exact_product(quantity, price)
    return FillPricing(reference_open, signed_spread, signed_slippage, price,
        reference_notional, fill_notional, exact_product(quantity, spread),
        exact_product(quantity, slippage), model.trading_fee_rate,
        exact_product(fill_notional, model.trading_fee_rate))
