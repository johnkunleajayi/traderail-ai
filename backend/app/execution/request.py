"""One approved new-exposure proposal, one economic-action/client-order identity."""
from dataclasses import dataclass, field
from datetime import datetime
import re
from app.risk.canonical import require, timestamp
from app.risk.vocabulary import Direction
from .authorization import ExecutionAuthorization
from .actions import intent_action
from app.strategies.decisions import DecisionType
from .canonical import execution_identity, seal
from .vocabulary import ExecutionEnvironment, OrderSide


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    authorization: ExecutionAuthorization
    venue_identity: str
    created_at: datetime
    action_identity: str = field(init=False)
    client_order_identity: str = field(init=False)
    order_identity: str = field(init=False)
    request_identity: str = field(init=False)

    def __post_init__(self):
        require(self.authorization, ExecutionAuthorization, "authorization")
        if self.environment is not ExecutionEnvironment.PAPER:
            raise ValueError("11A permits PAPER request contracts only; other environments are not enabled")
        if not isinstance(self.venue_identity, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.venue_identity):
            raise ValueError("Explicit canonical venue identity required")
        object.__setattr__(self, "created_at", timestamp(self.created_at))
        self.authorization.validate_at(self.created_at)
        intent_action(self.authorization.intent)
        action = execution_identity("action-1.0.0", {"intent": self.intent_identity})
        client = execution_identity("client-order-1.0.0", {"action": action})
        object.__setattr__(self, "action_identity", action)
        object.__setattr__(self, "client_order_identity", client)
        object.__setattr__(self, "order_identity", execution_identity("order-1.0.0", {"client": client}))
        seal(self, "request-1.0.0", "request_identity")

    @property
    def intent_identity(self): return self.authorization.intent.intent_identity
    @property
    def risk_decision_identity(self): return self.authorization.decision.decision_identity
    @property
    def environment(self): return self.authorization.intent.scope.environment
    @property
    def quantity(self): return self.authorization.quantity
    @property
    def action(self): return intent_action(self.authorization.intent)
    @property
    def side(self):
        return OrderSide.BUY if self.action is DecisionType.ENTER_LONG else OrderSide.SELL


def validate_retry(original: ExecutionRequest, retry: ExecutionRequest):
    """Pure equality guard, not a persisted deduplication or submission service."""
    require(original, ExecutionRequest, "original request")
    require(retry, ExecutionRequest, "retry request")
    if original.client_order_identity != retry.client_order_identity:
        raise ValueError("Retry must identify the same economic action")
    if original.request_identity != retry.request_identity:
        raise ValueError("Idempotency conflict: changed payload under one economic action")


@dataclass(frozen=True, slots=True)
class ApprovalConsumptionBinding:
    """Proposed uniqueness binding only. This does not mark an approval consumed."""
    request: ExecutionRequest
    consumption_key: str = field(init=False)
    binding_identity: str = field(init=False)

    def __post_init__(self):
        require(self.request, ExecutionRequest, "request")
        object.__setattr__(self, "consumption_key", execution_identity("approval-consumption-1.0.0",
            {"decision": self.request.risk_decision_identity}))
        seal(self, "consumption-binding-1.0.0", "binding_identity")
