"""Architecture vocabulary only: none of these values enable execution."""
from enum import Enum


class Environment(str, Enum):
    LAB = "LAB"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class RiskOutcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RiskProfile(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    GROWTH = "GROWTH"


class RiskState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    SUSPENDED = "SUSPENDED"


class KillSwitchScope(str, Enum):
    USER = "USER"
    PORTFOLIO = "PORTFOLIO"
    STRATEGY = "STRATEGY"
    MARKET = "MARKET"
    DATA = "DATA"
    SYSTEM = "SYSTEM"
