"""Lifecycle labels describe state; they grant no execution authority."""
from dataclasses import dataclass
from enum import Enum


class StrategyState(str, Enum):
    EXPERIMENTAL = "EXPERIMENTAL"
    BACKTESTED = "BACKTESTED"
    VALIDATED = "VALIDATED"
    PAPER = "PAPER"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"
    SHADOW = "SHADOW"
    LIMITED_LIVE = "LIMITED_LIVE"
    LIVE = "LIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class StrategyLifecycle:
    state: StrategyState

    def __post_init__(self):
        if not isinstance(self.state, StrategyState):
            raise ValueError("An explicit lifecycle state is required")
