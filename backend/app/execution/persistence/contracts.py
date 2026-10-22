"""Validated persistence inputs, never a venue or economic execution orchestrator."""
from dataclasses import dataclass, field
from datetime import datetime
from app.risk.canonical import require, timestamp
from ..canonical import seal
from ..request import ExecutionRequest
from ..orders import Order, validate_order_transition
from ..fills import Fill
from ..vocabulary import OrderState
from ..paper_fill.engine import PaperFillCalculation


@dataclass(frozen=True, slots=True)
class FillAudit:
    fill: Fill
    calculation: PaperFillCalculation
    audit_identity: str = field(init=False)

    def __post_init__(self):
        require(self.fill, Fill, "fill")
        require(self.calculation, PaperFillCalculation, "calculation")
        f, c = self.fill, self.calculation
        if (f.request != c.request or f.quantity != c.request.quantity
                or f.price != c.leg.modeled_execution_price or f.filled_at != c.executed_at):
            raise ValueError("Fill must exactly match request-bound 11B calculation evidence")
        seal(self, "persisted-fill-audit-1.0.0", "audit_identity")


@dataclass(frozen=True, slots=True)
class ExecutionArtifacts:
    request: ExecutionRequest
    consumed_at: datetime
    observations: tuple[Order, ...]
    fills: tuple[FillAudit, ...] = ()
    artifact_identity: str = field(init=False)

    def __post_init__(self):
        require(self.request, ExecutionRequest, "request")
        object.__setattr__(self, "consumed_at", timestamp(self.consumed_at))
        if self.consumed_at < self.request.created_at:
            raise ValueError("Consumption cannot precede request")
        self.request.authorization.validate_at(self.consumed_at)
        if not isinstance(self.observations, tuple) or not self.observations:
            raise ValueError("Explicit immutable lifecycle observations required")
        for o in self.observations:
            require(o, Order, "order observation")
            if o.request != self.request or o.observed_at < self.consumed_at:
                raise ValueError("Order/request/consumption context mismatch")
            if o.accepted_at is not None and o.accepted_at < self.consumed_at:
                raise ValueError("Acceptance cannot precede consumption")
        if self.observations[0].state is not OrderState.CREATED:
            raise ValueError("Lifecycle history must start at CREATED")
        for old, new in zip(self.observations, self.observations[1:]):
            validate_order_transition(old, new)
        if not isinstance(self.fills, tuple):
            raise ValueError("Immutable fill audits required")
        for audit in self.fills:
            require(audit, FillAudit, "fill audit")
            if audit.fill.request != self.request or audit.fill.filled_at < self.consumed_at:
                raise ValueError("Fill request/time mismatch")
        if len({a.fill.fill_identity for a in self.fills}) != len(self.fills):
            raise ValueError("Duplicate fill identity")
        if {f.observation_identity for f in self.observations[-1].fills} != {a.fill.observation_identity for a in self.fills}:
            raise ValueError("Complete exact fill audit coverage required")
        object.__setattr__(self, "fills", tuple(sorted(self.fills, key=lambda a:a.fill.fill_identity)))
        seal(self, "execution-artifacts-1.0.0", "artifact_identity")


@dataclass(frozen=True, slots=True)
class PersistenceReceipt:
    request_identity: str
    action_identity: str
    decision_identity: str
    consumption_identity: str
    client_order_identity: str
    order_identity: str
    observation_identity: str
    fill_identities: tuple[str, ...]
