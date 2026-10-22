"""Explicit trusted inputs only. No quote lookup, liquidity inference or clock."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from app.market_intelligence.types import DatasetContext
from app.risk.canonical import context, decimal, identity, require, timestamp
from app.risk.trade_risk.assumptions import TradeRiskCosts
from app.strategy_lab.costs import ResearchCostModel
from ..canonical import seal
from ..vocabulary import ExecutionEnvironment


@dataclass(frozen=True, slots=True)
class PaperMarketEvidence:
    request_identity: str
    instrument: DatasetContext
    environment: ExecutionEnvironment
    reference_price: Decimal
    priced_at: datetime
    observed_at: datetime
    valid_until: datetime
    source_identity: str
    is_valid: bool | None
    evidence_identity: str = field(init=False)

    def __post_init__(self):
        identity(self.request_identity, "request identity")
        context(self.instrument)
        require(self.environment, ExecutionEnvironment, "environment")
        decimal(self.reference_price, "reference price", positive=True)
        identity(self.source_identity, "source identity")
        if self.is_valid is not None: require(self.is_valid, bool, "evidence validity")
        for name in ("priced_at", "observed_at", "valid_until"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if not self.priced_at <= self.observed_at < self.valid_until:
            raise ValueError("Market evidence requires causal timestamps and bounded validity")
        seal(self, "paper-market-evidence-1.0.0", "evidence_identity")


@dataclass(frozen=True, slots=True)
class PaperFillAssumptions:
    cost_model: ResearchCostModel
    max_market_age: timedelta
    assumptions_identity: str = field(init=False)

    def __post_init__(self):
        require(self.cost_model, ResearchCostModel, "cost model")
        c = self.cost_model
        # Reuse the stricter existing Risk validation for both BUY and SELL:
        # full-spread/2 + slippage must leave a positive adverse sale price.
        TradeRiskCosts(c.trading_fee_rate, c.trading_fee_rate, c.spread_rate,
                       c.spread_rate, c.slippage_rate, c.slippage_rate)
        require(self.max_market_age, timedelta, "maximum market age")
        if self.max_market_age <= timedelta(0): raise ValueError("Maximum market age must be positive")
        seal(self, "paper-fill-assumptions-1.0.0", "assumptions_identity")
