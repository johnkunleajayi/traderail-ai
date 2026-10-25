"""Potential-loss sizing only; no authorization or exchange precision assumptions."""
from decimal import Decimal

from app.strategy_lab.numeric import difference, exact_product as mul, exact_sum as add
from app.risk.canonical import decimal, require
from app.risk.configuration import RiskConfiguration
from app.risk.intent import TradeIntent
from app.risk.portfolio import PortfolioRiskSnapshot
from app.risk.reasons import RiskReason
from app.risk.vocabulary import Direction, Environment
from .arithmetic import floor_increment, floor_ratio
from .assumptions import SizingAssumptions, TradeRiskCosts
from .models import (SizeProjection, SizingConstraint, TradeRiskCalculationError, TradeSizingResult, UnitLossModel)

ZERO = Decimal("0")
HALF = Decimal("0.5")


def unit_loss(direction: Direction, entry: Decimal, invalidation: Decimal, costs: TradeRiskCosts) -> UnitLossModel:
    require(direction, Direction, "direction")
    require(costs, TradeRiskCosts, "costs")
    decimal(entry, "assumed entry", positive=True)
    decimal(invalidation, "invalidation", positive=True)
    valid = invalidation < entry if direction is Direction.LONG else invalidation > entry
    if not valid:
        raise TradeRiskCalculationError(RiskReason.INVALID_INVALIDATION_BOUNDARY,
                                        "Invalidation is on the wrong side of the sizing reference")
    entry_spread = mul(entry, mul(costs.entry_full_spread_rate, HALF))
    exit_spread = mul(invalidation, mul(costs.exit_full_spread_rate, HALF))
    entry_slippage = mul(entry, costs.entry_slippage_rate)
    exit_slippage = mul(invalidation, costs.exit_slippage_rate)
    entry_adverse = add((entry_spread, entry_slippage))
    exit_adverse = add((exit_spread, exit_slippage))
    if direction is Direction.LONG:
        modeled_entry = add((entry, entry_adverse))
        modeled_exit = difference(invalidation, exit_adverse)
        price_loss = difference(entry, invalidation)
    else:
        modeled_entry = difference(entry, entry_adverse)
        modeled_exit = add((invalidation, exit_adverse))
        price_loss = difference(invalidation, entry)
    entry_fee = mul(modeled_entry, costs.entry_fee_rate)
    exit_fee = mul(modeled_exit, costs.exit_fee_rate)
    entry_total = add((entry_adverse, entry_fee))
    exit_total = add((exit_adverse, exit_fee))
    return UnitLossModel(entry, invalidation, price_loss, modeled_entry, modeled_exit,
        entry_spread, entry_slippage, entry_fee, exit_spread, exit_slippage, exit_fee,
        entry_total, exit_total, add((price_loss, entry_total, exit_total)))


def project_size(quantity: Decimal, model: UnitLossModel) -> SizeProjection:
    decimal(quantity, "quantity")
    require(model, UnitLossModel, "loss_model")
    reference_notional = mul(quantity, model.assumed_entry_price)
    # Never let adverse LONG entry pricing overrun a notional cap. For SHORT,
    # retain the higher reference rather than treating worse proceeds as capacity.
    notional = mul(quantity, max(model.assumed_entry_price, model.modeled_adverse_entry_price))
    entry_cost = mul(quantity, model.total_entry_cost)
    return SizeProjection(quantity, reference_notional, notional, entry_cost, mul(quantity, model.total_exit_cost),
                          mul(quantity, model.total_loss), add((reference_notional, entry_cost)))


def require_inputs(intent, configuration, snapshot, assumptions):
    for value, kind in ((intent, TradeIntent), (configuration, RiskConfiguration),
                        (snapshot, PortfolioRiskSnapshot), (assumptions, SizingAssumptions)):
        if type(value) is not kind:
            raise TradeRiskCalculationError(RiskReason.REQUIRED_RISK_DATA_UNAVAILABLE,
                                            "Explicit immutable trade-risk inputs are required")


def _calculate(intent, configuration, snapshot, assumptions):
    if (snapshot.scope != intent.scope or configuration.environment != intent.scope.environment
            or assumptions.entry_context.instrument != intent.instrument):
        raise TradeRiskCalculationError(RiskReason.RISK_CONTEXT_MISMATCH, "Trade-risk input contexts differ")
    quote_currency = intent.instrument.canonical_symbol.split(":")[0].split("/")[1]
    if quote_currency != snapshot.scope.base_currency:
        raise TradeRiskCalculationError(RiskReason.RISK_CURRENCY_UNSUPPORTED,
            "Quote-to-portfolio valuation is unavailable; currencies cannot be substituted")
    if intent.direction is Direction.SHORT and intent.scope.environment not in (Environment.LAB, Environment.PAPER):
        raise TradeRiskCalculationError(RiskReason.SHORT_RESEARCH_ONLY,
                                        "Short sizing is restricted to LAB/PAPER research")
    operating, hard = configuration.operating_limits, configuration.platform_hard_limits
    fraction = min(assumptions.requested_risk_fraction, operating.max_trade_risk_fraction, hard.max_trade_risk_fraction)
    budget = mul(snapshot.equity, fraction)
    model = unit_loss(intent.direction, assumptions.entry_reference_price, intent.invalidation_price, assumptions.costs)
    raw = floor_ratio(budget, model.total_loss)
    # This individual ceiling is necessary, but does NOT assess aggregate portfolio headroom.
    notional = mul(snapshot.equity, min(operating.max_gross_exposure_fraction, hard.max_gross_exposure_fraction))
    constraints = assumptions.constraints
    for cap in (constraints.platform_max_notional, constraints.operating_max_notional):
        if cap is not None:
            notional = min(notional, cap)
    # No borrowing or short-sale-proceeds reuse. SHORT uses the same conservative research reserve.
    cash = min(snapshot.available_cash, snapshot.equity)
    if constraints.max_entry_cash_commitment is not None:
        # Unit entry cash already includes adverse spread/slippage and entry fee,
        # but not exit costs. This caps sizing, never the authoritative snapshot.
        cash = min(cash, constraints.max_entry_cash_commitment)
    candidates = {
        SizingConstraint.RISK_BUDGET: raw,
        SizingConstraint.NOTIONAL_LIMIT: floor_ratio(notional, max(model.assumed_entry_price, model.modeled_adverse_entry_price)),
        SizingConstraint.AVAILABLE_CASH: floor_ratio(cash, add((model.assumed_entry_price, model.total_entry_cost))),
    }
    quantity_caps = tuple(v for v in (constraints.platform_max_quantity, constraints.operating_max_quantity) if v is not None)
    if quantity_caps:
        candidates[SizingConstraint.MAXIMUM_QUANTITY] = min(quantity_caps)
    quantity = min(candidates.values())
    bindings = {key for key, value in candidates.items() if value == quantity}
    if fraction == operating.max_trade_risk_fraction:
        bindings.add(SizingConstraint.OPERATING_RISK_LIMIT)
    if fraction == hard.max_trade_risk_fraction:
        bindings.add(SizingConstraint.PLATFORM_RISK_LIMIT)
    if constraints.quantity_increment is not None:
        rounded = floor_increment(quantity, constraints.quantity_increment)
        if rounded < quantity:
            bindings.add(SizingConstraint.QUANTITY_INCREMENT)
        quantity = rounded
    if constraints.minimum_quantity is not None and quantity < constraints.minimum_quantity:
        bindings.add(SizingConstraint.MINIMUM_QUANTITY)
        quantity = ZERO  # no admissible positive size; never round upward to a minimum
    return TradeSizingResult(intent.intent_identity, configuration.configuration_identity, snapshot.snapshot_identity,
        assumptions.assumptions_identity, intent.reference_price, fraction, budget, model, raw, quantity,
        notional, cash, project_size(quantity, model), tuple(bindings))


def calculate_trade_size(intent: TradeIntent, configuration: RiskConfiguration,
                         snapshot: PortfolioRiskSnapshot, assumptions: SizingAssumptions) -> TradeSizingResult:
    """Mathematical bound, not a freshness gate or permission to expose capital."""
    require_inputs(intent, configuration, snapshot, assumptions)
    try:
        return _calculate(intent, configuration, snapshot, assumptions)
    except ArithmeticError:
        raise TradeRiskCalculationError(RiskReason.NUMERIC_RISK_CALCULATION_UNAVAILABLE,
                                        "Finite conservative risk arithmetic could not be completed") from None
