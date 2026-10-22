"""Vocabulary is not a capability switch. Only PAPER requests exist in 11A."""
from enum import Enum
from app.risk.vocabulary import Environment as ExecutionEnvironment


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderState(str, Enum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class ExecutionState(str, Enum):
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class PositionLifecycleState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
