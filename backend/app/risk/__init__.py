"""Risk domain v1.0: contracts only; no execution authority or runtime wiring."""
from .canonical import DOMAIN_VERSION, to_wire
from .configuration import OperatingLimits, PlatformHardLimits, RiskConfiguration
from .context import MarketRiskContext, PortfolioScope
from .decision import RiskDecision
from .intent import TradeIntent
from .portfolio import PortfolioRiskSnapshot, PositionRiskSnapshot
from .reasons import RiskCategory, RiskFinding, RiskReason
from .vocabulary import Direction, Environment, KillSwitchScope, RiskOutcome, RiskProfile, RiskState

__all__ = ["DOMAIN_VERSION", "to_wire", "OperatingLimits", "PlatformHardLimits", "RiskConfiguration",
    "MarketRiskContext", "PortfolioScope", "RiskDecision", "TradeIntent", "PortfolioRiskSnapshot",
    "PositionRiskSnapshot", "RiskCategory", "RiskFinding", "RiskReason", "Direction", "Environment",
    "KillSwitchScope", "RiskOutcome", "RiskProfile", "RiskState"]
