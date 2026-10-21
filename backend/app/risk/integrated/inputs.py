"""One explicit immutable evaluation boundary. No precomputed results or bypass flags."""
from dataclasses import dataclass,field
from datetime import datetime

from app.risk.canonical import require,seal,timestamp
from app.risk.configuration import RiskConfiguration
from app.risk.context import MarketRiskContext
from app.risk.intent import TradeIntent
from app.risk.portfolio import PortfolioRiskSnapshot
from app.risk.trade_risk import SizingAssumptions
from app.risk.portfolio_risk import PortfolioRiskPolicy,PortfolioRiskContext
from app.risk.risk_states import RiskStatePolicy,DrawdownEvidence
from app.risk.safety_gate import SafetyGatePolicy,KillSwitchInventory,StrategyLifecycleEvidence,StrategyHealthEvidence


@dataclass(frozen=True,slots=True)
class RiskEvaluationInputs:
    intent: TradeIntent
    configuration: RiskConfiguration
    snapshot: PortfolioRiskSnapshot
    market_context: MarketRiskContext
    evaluated_at: datetime
    sizing_assumptions: SizingAssumptions | None
    portfolio_policy: PortfolioRiskPolicy | None
    portfolio_context: PortfolioRiskContext | None
    state_policy: RiskStatePolicy | None
    drawdown_evidence: DrawdownEvidence | None
    safety_policy: SafetyGatePolicy | None
    switch_inventory: KillSwitchInventory | None
    lifecycle_evidence: StrategyLifecycleEvidence | None
    health_evidence: StrategyHealthEvidence | None
    inputs_identity: str=field(init=False)

    def __post_init__(self):
        # Structurally absent root contracts cannot describe an exact-intent RiskDecision.
        # They fail at this boundary; no placeholders or permission are fabricated.
        for name,kind in (("intent",TradeIntent),("configuration",RiskConfiguration),
                          ("snapshot",PortfolioRiskSnapshot),("market_context",MarketRiskContext)):
            require(getattr(self,name),kind,name)
        object.__setattr__(self,"evaluated_at",timestamp(self.evaluated_at))
        for name,kind in (("sizing_assumptions",SizingAssumptions),("portfolio_policy",PortfolioRiskPolicy),
            ("portfolio_context",PortfolioRiskContext),("state_policy",RiskStatePolicy),("drawdown_evidence",DrawdownEvidence),
            ("safety_policy",SafetyGatePolicy),("switch_inventory",KillSwitchInventory),
            ("lifecycle_evidence",StrategyLifecycleEvidence),("health_evidence",StrategyHealthEvidence)):
            if getattr(self,name) is not None: require(getattr(self,name),kind,name)
        seal(self,"integrated-inputs-1.0.0","inputs_identity")
