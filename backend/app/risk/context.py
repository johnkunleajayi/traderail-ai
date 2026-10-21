"""Explicit portfolio ownership and immutable analytical provenance."""
from dataclasses import dataclass, field
from datetime import datetime
import re
from uuid import UUID

from app.market_intelligence.types import DatasetContext
from app.market_regimes.types import Regime
from .canonical import context, identity, require, seal, timestamp
from .vocabulary import Environment


@dataclass(frozen=True, slots=True)
class PortfolioScope:
    user_id: UUID
    portfolio_id: UUID
    environment: Environment
    base_currency: str

    def __post_init__(self):
        require(self.user_id, UUID, "user_id")
        require(self.portfolio_id, UUID, "portfolio_id")
        require(self.environment, Environment, "environment")
        if not isinstance(self.base_currency, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,15}", self.base_currency):
            raise ValueError("An explicit canonical valuation currency is required")


@dataclass(frozen=True, slots=True)
class MarketRiskContext:
    instrument: DatasetContext
    data_through: datetime
    observed_at: datetime
    expires_at: datetime
    data_identity: str
    intelligence_identity: str
    regime_identity: str
    regime: Regime
    context_identity: str = field(init=False)

    def __post_init__(self):
        context(self.instrument)
        require(self.regime, Regime, "regime")
        for name in ("data_through", "observed_at", "expires_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if not self.data_through <= self.observed_at < self.expires_at:
            raise ValueError("Market context must be causal and have a bounded validity interval")
        for name in ("data_identity", "intelligence_identity", "regime_identity"):
            identity(getattr(self, name), name)
        seal(self, "market-context", "context_identity")
