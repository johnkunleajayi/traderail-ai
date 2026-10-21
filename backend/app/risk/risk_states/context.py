"""Explicit trusted inputs only; a digest is provenance, not source authentication."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.risk.canonical import decimal, identity, require, seal, timestamp
from app.risk.context import PortfolioScope
from app.risk.vocabulary import RiskState


@dataclass(frozen=True, slots=True)
class DrawdownEvidence:
    scope: PortfolioScope
    snapshot_identity: str
    valuation_identity: str
    current_equity: Decimal
    historical_peak: Decimal
    previous_state: RiskState
    peak_as_of: datetime
    as_of: datetime
    expires_at: datetime
    source_identity: str
    evidence_identity: str = field(init=False)

    def __post_init__(self):
        require(self.scope, PortfolioScope, "scope")
        require(self.previous_state, RiskState, "previous state")
        for name in ("snapshot_identity", "valuation_identity", "source_identity"):
            identity(getattr(self, name), name)
        for name in ("current_equity", "historical_peak"):
            decimal(getattr(self, name), name)
        for name in ("peak_as_of", "as_of", "expires_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if not self.peak_as_of <= self.as_of < self.expires_at:
            raise ValueError("Peak must be observed no later than equity; validity must be bounded")
        seal(self, "drawdown-evidence-1.0.0", "evidence_identity")


@dataclass(frozen=True, slots=True)
class NewRiskCapacity:
    scope: PortfolioScope
    snapshot_identity: str
    configuration_identity: str
    intent_identity: str
    upstream_identity: str
    base_permitted_risk: Decimal
    proposed_modeled_risk: Decimal
    capacity_identity: str = field(init=False)

    def __post_init__(self):
        require(self.scope, PortfolioScope, "scope")
        for name in ("snapshot_identity", "configuration_identity", "intent_identity", "upstream_identity"):
            identity(getattr(self, name), name)
        for name in ("base_permitted_risk", "proposed_modeled_risk"):
            decimal(getattr(self, name), name)
        seal(self, "new-risk-capacity-1.0.0", "capacity_identity")
