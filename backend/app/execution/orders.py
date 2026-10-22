"""Immutable observed lifecycle and transition validation, never order submission."""
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from app.risk.canonical import require, text, timestamp
from app.strategy_lab.numeric import exact_sum
from .canonical import seal
from .fills import Fill
from .request import ExecutionRequest
from .vocabulary import OrderState as S, ExecutionState as E

TRANSITIONS = MappingProxyType({
    S.CREATED: frozenset((S.ACCEPTED, S.CANCELLED, S.REJECTED, S.UNKNOWN)),
    S.ACCEPTED: frozenset((S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.UNKNOWN)),
    S.PARTIALLY_FILLED: frozenset((S.FILLED, S.CANCELLED, S.UNKNOWN)),
    S.UNKNOWN: frozenset((S.ACCEPTED, S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.REJECTED)),
    S.FILLED: frozenset(), S.CANCELLED: frozenset(), S.REJECTED: frozenset(),
})
_EXECUTION_STATES = MappingProxyType({S.CREATED:E.READY, S.ACCEPTED:E.IN_PROGRESS,
    S.PARTIALLY_FILLED:E.IN_PROGRESS, S.FILLED:E.COMPLETED, S.CANCELLED:E.CANCELLED,
    S.REJECTED:E.FAILED, S.UNKNOWN:E.UNKNOWN})


@dataclass(frozen=True, slots=True)
class Order:
    request: ExecutionRequest
    state: S
    observed_at: datetime
    accepted_at: datetime | None
    venue_order_identity: str | None
    fills: tuple[Fill, ...]
    observation_identity: str = field(init=False)

    def __post_init__(self):
        require(self.request, ExecutionRequest, "request")
        require(self.state, S, "order state")
        object.__setattr__(self, "observed_at", timestamp(self.observed_at))
        if self.observed_at < self.request.created_at:
            raise ValueError("Order observation cannot precede request creation")
        if self.accepted_at is not None:
            object.__setattr__(self, "accepted_at", timestamp(self.accepted_at))
            if not self.request.created_at <= self.accepted_at <= self.observed_at:
                raise ValueError("Acceptance must be causal")
            self.request.authorization.validate_at(self.accepted_at)
        if self.venue_order_identity is not None: text(self.venue_order_identity, "venue order identity")
        if not isinstance(self.fills, tuple) or any(type(f) is not Fill for f in self.fills):
            raise ValueError("Immutable typed fill observations required")
        if len({f.fill_identity for f in self.fills}) != len(self.fills):
            raise ValueError("Duplicate/conflicting venue fill identity")
        for fill in self.fills:
            if (fill.request_identity != self.request.request_identity or self.accepted_at is None
                    or not self.accepted_at <= fill.filled_at <= self.observed_at):
                raise ValueError("Fill provenance/time must match accepted order")
        object.__setattr__(self, "fills", tuple(sorted(self.fills, key=lambda f:(f.filled_at, f.fill_identity))))
        total = self.filled_quantity
        if total > self.request.quantity: raise ValueError("Cumulative fills exceed approved quantity")
        if self.state in (S.CREATED, S.REJECTED) and (self.accepted_at is not None or total != 0):
            raise ValueError("Unaccepted/rejected order cannot have acceptance or fills")
        if self.state in (S.ACCEPTED, S.PARTIALLY_FILLED, S.FILLED) and self.accepted_at is None:
            raise ValueError("Accepted lifecycle needs explicit acceptance time")
        if self.state is S.ACCEPTED and total != 0: raise ValueError("Use partial/filled state for fills")
        if self.state is S.PARTIALLY_FILLED and not 0 < total < self.request.quantity:
            raise ValueError("Partial state requires a positive incomplete fill quantity")
        if self.state is S.FILLED and total != self.request.quantity:
            raise ValueError("Filled state requires the exact approved quantity")
        if self.state not in (S.FILLED,) and total == self.request.quantity:
            raise ValueError("Complete known fills require FILLED state")
        seal(self, "order-observation-1.0.0", "observation_identity")

    @property
    def order_identity(self): return self.request.order_identity
    @property
    def filled_quantity(self): return exact_sum(f.quantity for f in self.fills)
    @property
    def execution_state(self): return _EXECUTION_STATES[self.state]


def validate_order_transition(previous: Order, current: Order):
    require(previous, Order, "previous order")
    require(current, Order, "current order")
    if previous.request.request_identity != current.request.request_identity:
        raise ValueError("Order cannot change execution request or authorization")
    if current.observed_at < previous.observed_at:
        raise ValueError("Order observations must be chronological")
    if previous.venue_order_identity is not None and current.venue_order_identity != previous.venue_order_identity:
        raise ValueError("Known venue order attribution cannot change/disappear")
    if previous.accepted_at is not None and current.accepted_at != previous.accepted_at:
        raise ValueError("Known acceptance cannot change/disappear")
    old = {f.fill_identity:f.observation_identity for f in previous.fills}
    new = {f.fill_identity:f.observation_identity for f in current.fills}
    if any(new.get(key) != value for key,value in old.items()):
        raise ValueError("Observed fills cannot disappear or be rewritten")
    if current.state is not previous.state and current.state not in TRANSITIONS[previous.state]:
        raise ValueError("Invalid order state transition")
    if not TRANSITIONS[previous.state] and (old != new or previous.accepted_at != current.accepted_at
            or previous.venue_order_identity != current.venue_order_identity):
        raise ValueError("Terminal order facts cannot change")
