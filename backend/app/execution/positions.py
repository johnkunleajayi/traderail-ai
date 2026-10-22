"""Lifecycle/provenance references only, not a position ledger or mutation."""
from dataclasses import dataclass, field
from datetime import datetime
from app.risk.canonical import identity, require, timestamp
from .canonical import execution_identity, seal
from .request import ExecutionRequest
from .vocabulary import PositionLifecycleState as S


@dataclass(frozen=True, slots=True)
class PositionLifecycleReference:
    opening_request: ExecutionRequest
    state: S
    closing_order_identities: tuple[str, ...]
    observed_at: datetime
    position_identity: str = field(init=False)
    reference_identity: str = field(init=False)

    def __post_init__(self):
        require(self.opening_request, ExecutionRequest, "opening request")
        require(self.state, S, "position lifecycle state")
        object.__setattr__(self, "observed_at", timestamp(self.observed_at))
        if self.observed_at < self.opening_request.created_at: raise ValueError("Position reference must be causal")
        if not isinstance(self.closing_order_identities, tuple): raise ValueError("Immutable closing references required")
        for row in self.closing_order_identities: identity(row, "closing order identity")
        if (len(set(self.closing_order_identities)) != len(self.closing_order_identities)
                or self.opening_request.order_identity in self.closing_order_identities):
            raise ValueError("Duplicate/self-closing order reference")
        if (self.state is S.CLOSED) != bool(self.closing_order_identities):
            raise ValueError("CLOSED requires declared closing provenance; OPEN cannot claim closure")
        object.__setattr__(self, "closing_order_identities", tuple(sorted(self.closing_order_identities)))
        object.__setattr__(self, "position_identity", execution_identity("position-reference-1.0.0",
            {"opening_client_order": self.opening_request.client_order_identity}))
        seal(self, "position-lifecycle-reference-1.0.0", "reference_identity")


def validate_position_reference_transition(previous: PositionLifecycleReference, current: PositionLifecycleReference):
    """Validate reference progression only; never open or close a ledger position."""
    require(previous, PositionLifecycleReference, "previous reference")
    require(current, PositionLifecycleReference, "current reference")
    if previous.opening_request.request_identity != current.opening_request.request_identity:
        raise ValueError("Position reference cannot change its opening provenance")
    if current.observed_at < previous.observed_at:
        raise ValueError("Position references must be chronological")
    if previous.state is S.CLOSED and (current.state is not S.CLOSED
            or current.closing_order_identities != previous.closing_order_identities):
        raise ValueError("Closed lifecycle references cannot reopen or rewrite closure")
