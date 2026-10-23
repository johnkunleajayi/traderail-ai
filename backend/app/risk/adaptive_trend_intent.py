"""Server-side thesis boundary from the exact analytical entry observation.

This constructs a proposal for Risk, never an executable stop or approval.
Callers must supply trusted analytical evidence, not HTTP financial values.
"""
from decimal import Decimal

from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.contract import evaluate_strategy
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.inputs import StrategyInput
from app.strategy_lab.numeric import difference, exact_product, exact_sum
from .canonical import decimal, require
from .context import MarketRiskContext
from .intent import TradeIntent
from .vocabulary import Direction

INVALIDATION_RULE = "adaptive-trend-atr14-invalidation/v1.0.0"
ATR_MULTIPLIER = Decimal("2")


def create_adaptive_trend_intent(*, scope, observation: StrategyInput,
                                decision: StrategyDecision,
                                market_context: MarketRiskContext,
                                created_at, expires_at) -> TradeIntent:
    """Use the canonical decision close as entry reference; accept no price override.

    Reconcile the supplied decision against unchanged v1.0.0 defaults. ATR is read
    from its existing FeatureRow, never recalculated or fetched independently.
    """
    require(observation, StrategyInput, "observation")
    require(decision, StrategyDecision, "decision")
    require(market_context, MarketRiskContext, "market_context")
    strategy = AdaptiveTrendStrategy()
    if observation.definition != strategy.definition:
        raise ValueError("Adaptive Trend v1.0.0 default configuration required")
    expected = evaluate_strategy(strategy, observation)
    if decision != expected:
        raise ValueError("Decision must match the exact authoritative strategy observation")
    directions = {DecisionType.ENTER_LONG: Direction.LONG,
                  DecisionType.ENTER_SHORT: Direction.SHORT}
    if decision.decision not in directions:
        raise ValueError("An entry decision is required for a new-risk proposal")
    if (market_context.instrument != observation.context
            or market_context.data_through != observation.candle.close_time
            or market_context.observed_at != observation.as_of
            or market_context.regime != observation.regime.regime):
        raise ValueError("Market context must match the strategy observation")
    atr = decimal(observation.intelligence.atr_14, "ATR14", positive=True)
    entry = decimal(observation.candle.close, "entry reference", positive=True)
    displacement = exact_product(ATR_MULTIPLIER, atr)
    direction = directions[decision.decision]
    invalidation = (difference(entry, displacement) if direction is Direction.LONG
                    else exact_sum((entry, displacement)))
    decimal(invalidation, "invalidation price", positive=True)
    return TradeIntent(scope, observation.context, direction, observation.definition,
        decision.decision_identity, observation.input_identity, decision.timestamp,
        entry, invalidation, {"invalidation_rule": INVALIDATION_RULE,
            "atr_14": atr, "atr_multiplier": ATR_MULTIPLIER,
            "analytical_input_identity": observation.input_identity,
            "intelligence_identity": market_context.intelligence_identity,
            "reason_codes": decision.reason_codes}, market_context, created_at, expires_at)
