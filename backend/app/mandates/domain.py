"""User preferences and lifecycle; deliberately independent of trading domains."""
from enum import StrEnum

class RiskLevel(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    GROWTH = "GROWTH"

class TradingStyle(StrEnum):
    SHORT_TERM = "SHORT_TERM"
    MEDIUM_TERM = "MEDIUM_TERM"
    LONGER_TERM = "LONGER_TERM"

class MandateStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"

class MandateError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status

def require_editable(status: str) -> None:
    if status not in (MandateStatus.DRAFT, MandateStatus.PAUSED):
        raise MandateError("mandate_not_editable", "Pause an active mandate before editing; retired mandates cannot be edited")

def require_transition(current: str, target: MandateStatus) -> None:
    transitions = {
        MandateStatus.DRAFT: (MandateStatus.ACTIVE, MandateStatus.RETIRED),
        MandateStatus.ACTIVE: (MandateStatus.PAUSED, MandateStatus.RETIRED),
        MandateStatus.PAUSED: (MandateStatus.ACTIVE, MandateStatus.RETIRED),
        MandateStatus.RETIRED: (),
    }
    if current not in transitions or (current != target and target not in transitions[current]):
        raise MandateError("invalid_mandate_transition", "This mandate lifecycle transition is not permitted")
