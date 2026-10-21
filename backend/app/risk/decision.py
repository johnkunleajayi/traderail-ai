"""Bound evaluation records only. No evaluator, sizing, order or execution adapter."""
from dataclasses import dataclass, field
from datetime import datetime

from .canonical import require, seal, timestamp
from .configuration import RiskConfiguration
from .context import MarketRiskContext
from .intent import TradeIntent
from .portfolio import PortfolioRiskSnapshot
from .reasons import RiskFinding, RiskReason
from .vocabulary import RiskOutcome


@dataclass(frozen=True, slots=True)
class RiskDecision:
    intent: TradeIntent
    configuration: RiskConfiguration
    portfolio_snapshot: PortfolioRiskSnapshot
    market_context: MarketRiskContext
    evaluated_at: datetime
    outcome: RiskOutcome
    findings: tuple[RiskFinding, ...]
    approval_expires_at: datetime | None
    decision_identity: str = field(init=False)

    def __post_init__(self):
        for name, kind in (("intent", TradeIntent), ("configuration", RiskConfiguration),
                           ("portfolio_snapshot", PortfolioRiskSnapshot), ("market_context", MarketRiskContext),
                           ("outcome", RiskOutcome)):
            require(getattr(self, name), kind, name)
        object.__setattr__(self, "evaluated_at", timestamp(self.evaluated_at))
        # Integrated rejection may retain bad context for audit; approval is never relaxed.
        rejection_audit = (self.outcome is RiskOutcome.REJECTED and isinstance(self.findings, (tuple, list))
            and any(type(row) is RiskFinding and row.code is RiskReason.INTEGRATED_CONTEXT_MISMATCH for row in self.findings))
        if (self.portfolio_snapshot.scope != self.intent.scope
                or self.configuration.environment != self.intent.scope.environment
                or self.market_context.instrument != self.intent.instrument):
            if not rejection_audit:
                raise ValueError("Decision context must match exact intent scope, environment and instrument")
        if self.evaluated_at < max(self.intent.created_at, self.portfolio_snapshot.as_of, self.market_context.observed_at):
            if not rejection_audit:
                raise ValueError("Evaluation cannot precede its inputs")
        if not isinstance(self.findings, (tuple, list)) or not self.findings:
            raise ValueError("Explicit typed risk findings are required")
        for finding in self.findings:
            require(finding, RiskFinding, "finding")
        if len({finding.code for finding in self.findings}) != len(self.findings):
            raise ValueError("Duplicate risk reason")
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda item: item.code.value)))
        rejected = any(item.code.outcome is RiskOutcome.REJECTED for item in self.findings)
        if self.outcome is RiskOutcome.APPROVED:
            if rejected or self.approval_expires_at is None:
                raise ValueError("Approval requires affirmative findings and explicit expiry")
            expiry = timestamp(self.approval_expires_at)
            object.__setattr__(self, "approval_expires_at", expiry)
            ceiling = min(self.intent.expires_at, self.intent.market_context.expires_at,
                          self.portfolio_snapshot.expires_at, self.market_context.expires_at)
            if (not self.evaluated_at < expiry <= ceiling
                    or expiry - self.evaluated_at > self.configuration.operating_limits.max_approval_age):
                raise ValueError("Approval must expire within every evaluated input's validity and configured age")
        elif not rejected or self.approval_expires_at is not None:
            raise ValueError("Rejection requires a rejection reason and cannot carry approval expiry")
        seal(self, "decision", "decision_identity")

    @property
    def intent_identity(self):
        return self.intent.intent_identity

    @property
    def configuration_identity(self):
        return self.configuration.configuration_identity

    @property
    def snapshot_identity(self):
        return self.portfolio_snapshot.snapshot_identity

    @property
    def market_context_identity(self):
        return self.market_context.context_identity

    @property
    def reason_codes(self):
        return tuple(finding.code for finding in self.findings)
