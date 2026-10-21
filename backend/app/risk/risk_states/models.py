"""Immutable scoped state evidence. No final RiskDecision or execution contract."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from app.risk.canonical import decimal, identity, require, seal, timestamp
from app.risk.reasons import RiskFinding
from app.risk.vocabulary import RiskOutcome, RiskState
from app.strategy_lab.numeric import exact_product
from .measurement import DrawdownMeasurement, StateTransition, transition_state
from .policy import RiskStatePolicy

RISK_STATE_VERSION = "1.0.0"


class RiskStateStatus(str, Enum):
    RISK_STATE_PASS = "RISK_STATE_PASS"
    RISK_STATE_REJECTED = "RISK_STATE_REJECTED"


@dataclass(frozen=True, slots=True)
class RiskStateProjection:
    measurement: DrawdownMeasurement
    policy: RiskStatePolicy
    previous_state: RiskState
    evaluated_state: RiskState
    transition: StateTransition
    multiplier: Decimal
    base_permitted_risk: Decimal
    adjusted_permitted_risk: Decimal
    proposed_modeled_risk: Decimal
    projection_identity: str = field(init=False)

    def __post_init__(self):
        require(self.measurement, DrawdownMeasurement, "measurement")
        require(self.policy, RiskStatePolicy, "policy")
        require(self.previous_state, RiskState, "previous state")
        require(self.evaluated_state, RiskState, "evaluated state")
        require(self.transition, StateTransition, "transition")
        for name in ("multiplier", "base_permitted_risk", "adjusted_permitted_risk", "proposed_modeled_risk"):
            decimal(getattr(self, name), name, fraction=name == "multiplier")
        if (transition_state(self.measurement, self.previous_state, self.policy) != (self.evaluated_state, self.transition)
                or self.multiplier != self.policy.multiplier(self.evaluated_state)
                or self.adjusted_permitted_risk != exact_product(self.base_permitted_risk, self.multiplier)):
            raise ValueError("State projection must reconcile exactly with its explicit policy")
        seal(self, "state-projection-1.0.0", "projection_identity")

    @property
    def permits_new_risk(self):
        return (self.evaluated_state is not RiskState.SUSPENDED and self.adjusted_permitted_risk > 0
                and self.proposed_modeled_risk > 0 and self.proposed_modeled_risk <= self.adjusted_permitted_risk)


@dataclass(frozen=True, slots=True)
class RiskStateEvaluation:
    snapshot_identity: str | None
    configuration_identity: str | None
    policy_identity: str | None
    evidence_identity: str | None
    capacity_identity: str | None
    evaluated_at: datetime
    status: RiskStateStatus
    projection: RiskStateProjection | None
    findings: tuple[RiskFinding, ...]
    scope: str = field(default="RISK_STATE_ONLY", init=False)
    version: str = field(default=RISK_STATE_VERSION, init=False)
    evaluation_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("snapshot_identity", "configuration_identity", "policy_identity", "evidence_identity", "capacity_identity"):
            if getattr(self, name) is not None:
                identity(getattr(self, name), name)
        object.__setattr__(self, "evaluated_at", timestamp(self.evaluated_at))
        require(self.status, RiskStateStatus, "status")
        if not isinstance(self.findings, (tuple, list)) or not self.findings:
            raise ValueError("Typed findings required")
        for row in self.findings:
            require(row, RiskFinding, "finding")
        if len({row.code for row in self.findings}) != len(self.findings):
            raise ValueError("Duplicate findings")
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda row: row.code.value)))
        rejected = any(row.code.outcome is RiskOutcome.REJECTED for row in self.findings)
        if self.projection is not None:
            require(self.projection, RiskStateProjection, "projection")
            if self.policy_identity != self.projection.policy.policy_identity:
                raise ValueError("Projection policy identity mismatch")
            if self.projection.permits_new_risk == rejected:
                raise ValueError("Findings must agree with state capacity")
        if self.status is RiskStateStatus.RISK_STATE_PASS:
            if rejected or self.projection is None or any(getattr(self, name) is None for name in
                    ("snapshot_identity", "configuration_identity", "policy_identity", "evidence_identity", "capacity_identity")):
                raise ValueError("A state pass requires complete passing evidence")
        elif not rejected:
            raise ValueError("Rejection requires a rejecting finding")
        seal(self, "state-evaluation-1.0.0", "evaluation_identity")
