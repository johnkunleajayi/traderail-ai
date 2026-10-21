"""Immutable portfolio projections and scoped gate evidence; no RiskDecision."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType

from app.risk.canonical import decimal, identity, require, seal, symbol, timestamp
from app.risk.portfolio import PositionRiskSnapshot
from app.risk.reasons import RiskFinding, RiskReason
from app.risk.vocabulary import Direction, RiskOutcome
from app.strategy_lab.numeric import difference, exact_sum

PORTFOLIO_RISK_VERSION = "1.0.0"


class PortfolioRiskStatus(str, Enum):
    PORTFOLIO_RISK_PASS = "PORTFOLIO_RISK_PASS"
    PORTFOLIO_RISK_REJECTED = "PORTFOLIO_RISK_REJECTED"


class PortfolioLimitKind(str, Enum):
    OPEN_RISK = "OPEN_RISK"
    GROSS_EXPOSURE = "GROSS_EXPOSURE"
    INSTRUMENT = "INSTRUMENT"
    DIRECTION = "DIRECTION"
    STRATEGY = "STRATEGY"
    CORRELATED_EXPOSURE = "CORRELATED_EXPOSURE"
    GROUP = "GROUP"
    POSITION_COUNT = "POSITION_COUNT"
    CAPITAL_COMMITMENT = "CAPITAL_COMMITMENT"


class PortfolioRiskInputError(ValueError):
    def __init__(self, code, message):
        require(code, RiskReason, "code")
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PortfolioState:
    equity: Decimal
    available_cash: Decimal
    open_risk: Decimal
    gross_exposure: Decimal
    long_exposure: Decimal
    short_exposure: Decimal
    position_count: int
    instrument_exposure: Mapping[str, Decimal]
    strategy_exposure: Mapping[str, Decimal]
    strategy_configuration_exposure: Mapping[str, Decimal]
    state_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("equity", "open_risk", "gross_exposure", "long_exposure", "short_exposure"):
            decimal(getattr(self, name), name, positive=name == "equity")
        if not isinstance(self.available_cash, Decimal) or not self.available_cash.is_finite():
            raise ValueError("Finite cash required, including negative rejected projection evidence")
        require(self.position_count, int, "position_count")
        if self.position_count < 0:
            raise ValueError("Negative position count")
        for name in ("instrument_exposure", "strategy_exposure", "strategy_configuration_exposure"):
            rows = getattr(self, name)
            if not isinstance(rows, Mapping):
                raise ValueError("Exposure mappings required")
            for key, value in rows.items():
                if not isinstance(key, str):
                    raise ValueError("Exposure keys must be strings")
                symbol(key) if name == "instrument_exposure" else identity(key, name)
                decimal(value, name)
            object.__setattr__(self, name, MappingProxyType(dict(rows)))
        if (exact_sum((self.long_exposure, self.short_exposure)) != self.gross_exposure
                or any(exact_sum(getattr(self, name).values()) != self.gross_exposure for name in
                       ("instrument_exposure", "strategy_exposure", "strategy_configuration_exposure"))):
            raise ValueError("Portfolio exposure dimensions must reconcile without netting")
        seal(self, "portfolio-state-1.0.0", "state_identity")


@dataclass(frozen=True, slots=True)
class PortfolioLimitCheck:
    kind: PortfolioLimitKind
    key: str
    before_value: Decimal
    proposed_delta: Decimal
    after_value: Decimal
    limit_value: Decimal
    before_fraction: Decimal | None
    after_fraction: Decimal | None
    limit_fraction: Decimal | None
    check_identity: str = field(init=False)

    def __post_init__(self):
        require(self.kind, PortfolioLimitKind, "kind")
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("Explicit check key required")
        for name in ("before_value", "proposed_delta", "after_value", "limit_value",
                     "before_fraction", "after_fraction", "limit_fraction"):
            value = getattr(self, name)
            if value is not None:
                decimal(value, name)
            elif name in ("before_value", "proposed_delta", "after_value", "limit_value"):
                raise ValueError("Known values required")
        seal(self, "portfolio-limit-check-1.0.0", "check_identity")

    @property
    def within_limit(self):
        return self.after_value <= self.limit_value


@dataclass(frozen=True, slots=True)
class CorrelationContribution:
    position_identity: str
    canonical_symbol: str
    direction: Direction
    coefficient: Decimal
    direction_adjusted_coefficient: Decimal
    included: bool
    exposure_amount: Decimal
    evidence_identity: str | None

    def __post_init__(self):
        identity(self.position_identity, "position_identity")
        symbol(self.canonical_symbol)
        require(self.direction, Direction, "direction")
        require(self.included, bool, "included")
        decimal(self.exposure_amount, "exposure_amount")
        for value in (self.coefficient, self.direction_adjusted_coefficient):
            if not isinstance(value, Decimal) or not value.is_finite() or not -1 <= value <= 1:
                raise ValueError("Finite correlation required")
        if self.evidence_identity is not None:
            identity(self.evidence_identity, "evidence_identity")


@dataclass(frozen=True, slots=True)
class PortfolioProjection:
    before: PortfolioState
    after: PortfolioState
    canonical_symbol: str
    direction: Direction
    strategy_identity: str
    strategy_configuration_identity: str
    group_codes: tuple[str, ...]
    proposed_risk: Decimal
    proposed_exposure: Decimal
    proposed_cash_commitment: Decimal
    proposed_base_quantity: Decimal
    existing_positions: tuple[PositionRiskSnapshot, ...]
    checks: tuple[PortfolioLimitCheck, ...]
    correlation_contributions: tuple[CorrelationContribution, ...]
    projection_identity: str = field(init=False)

    def __post_init__(self):
        require(self.before, PortfolioState, "before")
        require(self.after, PortfolioState, "after")
        for name in ("proposed_risk", "proposed_exposure", "proposed_cash_commitment", "proposed_base_quantity"):
            decimal(getattr(self, name), name, positive=True)
        for name, kind in (("existing_positions", PositionRiskSnapshot), ("checks", PortfolioLimitCheck),
                           ("correlation_contributions", CorrelationContribution)):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError("Immutable tuple required")
            for row in getattr(self, name):
                require(row, kind, name)
        symbol(self.canonical_symbol)
        require(self.direction, Direction, "direction")
        identity(self.strategy_identity, "strategy_identity")
        identity(self.strategy_configuration_identity, "strategy_configuration_identity")
        if not isinstance(self.group_codes, tuple) or len(set(self.group_codes)) != len(self.group_codes):
            raise ValueError("Explicit distinct immutable group declarations required")
        expected = {(PortfolioLimitKind.OPEN_RISK, "TOTAL"), (PortfolioLimitKind.GROSS_EXPOSURE, "TOTAL"),
                    (PortfolioLimitKind.POSITION_COUNT, "TOTAL"), (PortfolioLimitKind.CAPITAL_COMMITMENT, "NEW_RESERVE"),
                    (PortfolioLimitKind.CORRELATED_EXPOSURE, self.canonical_symbol)}
        expected.update((PortfolioLimitKind.DIRECTION, d.value) for d in Direction)
        expected.update((PortfolioLimitKind.INSTRUMENT, k) for k in self.after.instrument_exposure)
        expected.update((PortfolioLimitKind.STRATEGY, k) for k in self.after.strategy_exposure)
        expected.update((PortfolioLimitKind.GROUP, k) for k in self.group_codes)
        if {(row.kind, row.key) for row in self.checks} != expected:
            raise ValueError("Mandatory portfolio checks cannot be omitted")
        if (self.after.equity != self.before.equity
                or self.before.position_count != len(self.existing_positions)
                or self.after.position_count != self.before.position_count + 1
                or self.after.open_risk != exact_sum((self.before.open_risk, self.proposed_risk))
                or self.after.gross_exposure != exact_sum((self.before.gross_exposure, self.proposed_exposure))
                or self.after.available_cash != difference(self.before.available_cash, self.proposed_cash_commitment)):
            raise ValueError("Before/after portfolio arithmetic must reconcile")
        for direction in Direction:
            name = "long_exposure" if direction is Direction.LONG else "short_exposure"
            delta = self.proposed_exposure if direction is self.direction else Decimal("0")
            if getattr(self.after, name) != exact_sum((getattr(self.before, name), delta)):
                raise ValueError("Directional exposure cannot be netted or shifted")
        if ({row.position_identity for row in self.correlation_contributions} != {p.position_identity for p in self.existing_positions}
                or len(self.correlation_contributions) != len(self.existing_positions)):
            raise ValueError("Correlation evaluation must retain every existing position")
        keys = [(row.kind.value, row.key) for row in self.checks]
        if len(set(keys)) != len(keys) or not keys:
            raise ValueError("Distinct portfolio checks required")
        object.__setattr__(self, "checks", tuple(sorted(self.checks, key=lambda c: (c.kind.value, c.key))))
        seal(self, "portfolio-projection-1.0.0", "projection_identity")

    @property
    def binding_limits(self):
        return tuple(c.kind.value + ":" + c.key for c in self.checks if c.after_value >= c.limit_value)


@dataclass(frozen=True, slots=True)
class PortfolioRiskEvaluation:
    intent_identity: str
    trade_risk_identity: str
    configuration_identity: str
    snapshot_identity: str
    policy_identity: str
    context_identity: str
    evaluated_at: datetime
    status: PortfolioRiskStatus
    projection: PortfolioProjection | None
    findings: tuple[RiskFinding, ...]
    scope: str = field(default="PORTFOLIO_RISK_ONLY", init=False)
    version: str = field(default=PORTFOLIO_RISK_VERSION, init=False)
    evaluation_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("intent_identity", "trade_risk_identity", "configuration_identity", "snapshot_identity", "policy_identity", "context_identity"):
            identity(getattr(self, name), name)
        object.__setattr__(self, "evaluated_at", timestamp(self.evaluated_at))
        require(self.status, PortfolioRiskStatus, "status")
        if not isinstance(self.findings, tuple) or not self.findings:
            raise ValueError("Typed findings required")
        for row in self.findings:
            require(row, RiskFinding, "finding")
        if len({r.code for r in self.findings}) != len(self.findings):
            raise ValueError("Duplicate reason")
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda f: f.code.value)))
        if self.projection is not None:
            require(self.projection, PortfolioProjection, "projection")
        rejected = any(f.code.outcome is RiskOutcome.REJECTED for f in self.findings)
        if self.status is PortfolioRiskStatus.PORTFOLIO_RISK_PASS:
            if (rejected or self.projection is None or self.projection.after.available_cash < 0
                    or any(not c.within_limit for c in self.projection.checks)):
                raise ValueError("Portfolio pass requires complete passing projection evidence")
        elif not rejected:
            raise ValueError("Rejection requires a typed rejection finding")
        seal(self, "portfolio-risk-evaluation-1.0.0", "evaluation_identity")
