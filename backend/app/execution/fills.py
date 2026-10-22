"""Observed fill facts only. No pricing, simulation, fee or P&L calculation."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from app.risk.canonical import decimal, require, text, timestamp
from .canonical import execution_identity, seal
from .request import ExecutionRequest


@dataclass(frozen=True, slots=True)
class Fill:
    request: ExecutionRequest
    venue_fill_identity: str
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    fill_identity: str = field(init=False)
    observation_identity: str = field(init=False)

    def __post_init__(self):
        require(self.request, ExecutionRequest, "request")
        text(self.venue_fill_identity, "venue fill identity")
        decimal(self.quantity, "fill quantity", positive=True)
        decimal(self.price, "fill price", positive=True)
        object.__setattr__(self, "filled_at", timestamp(self.filled_at))
        if self.quantity > self.request.quantity or self.filled_at < self.request.created_at:
            raise ValueError("Fill must fit the exact request and follow its creation")
        object.__setattr__(self, "fill_identity", execution_identity("fill-1.0.0",
            {"order": self.request.order_identity, "venue_fill": self.venue_fill_identity}))
        seal(self, "fill-observation-1.0.0", "observation_identity")

    @property
    def order_identity(self): return self.request.order_identity
    @property
    def client_order_identity(self): return self.request.client_order_identity
    @property
    def request_identity(self): return self.request.request_identity
    @property
    def intent_identity(self): return self.request.intent_identity
    @property
    def risk_decision_identity(self): return self.request.risk_decision_identity
