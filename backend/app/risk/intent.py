"""A strategy proposal with analytical provenance; deliberately not an order."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.market_intelligence.types import DatasetContext
from app.strategies.configuration import StrategyDefinition
from .canonical import context, decimal, freeze_evidence, identity, require, seal, timestamp
from .context import MarketRiskContext, PortfolioScope
from .vocabulary import Direction


@dataclass(frozen=True, slots=True)
class TradeIntent:
    scope: PortfolioScope
    instrument: DatasetContext
    direction: Direction
    strategy: StrategyDefinition
    strategy_decision_identity: str
    strategy_input_identity: str
    decision_at: datetime
    reference_price: Decimal
    invalidation_price: Decimal
    entry_context: Mapping
    market_context: MarketRiskContext
    created_at: datetime
    expires_at: datetime
    intent_identity: str = field(init=False)

    def __post_init__(self):
        require(self.scope, PortfolioScope, "scope")
        context(self.instrument)
        require(self.direction, Direction, "direction")
        require(self.strategy, StrategyDefinition, "strategy")
        require(self.market_context, MarketRiskContext, "market_context")
        for name in ("strategy_decision_identity", "strategy_input_identity"):
            identity(getattr(self, name), name)
        for name in ("decision_at", "created_at", "expires_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if self.instrument != self.market_context.instrument or self.decision_at != self.market_context.data_through:
            raise ValueError("Proposal instrument and analytical decision boundary must match its market context")
        if not self.market_context.observed_at <= self.created_at < self.expires_at:
            raise ValueError("Intent creation must follow available evidence and precede expiry")
        decimal(self.reference_price, "reference_price", positive=True)
        decimal(self.invalidation_price, "invalidation_price", positive=True)
        valid = (self.invalidation_price < self.reference_price if self.direction is Direction.LONG
                 else self.invalidation_price > self.reference_price)
        if not valid:
            raise ValueError("Invalidation must be below LONG reference or above SHORT reference")
        frozen = freeze_evidence(self.entry_context)
        if not frozen:
            raise ValueError("An explicit descriptive entry context is required")
        object.__setattr__(self, "entry_context", frozen)
        seal(self, "trade-intent", "intent_identity")
