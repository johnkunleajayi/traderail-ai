"""Immutable gate evidence, not another final-decision type."""
from dataclasses import dataclass,field
from enum import Enum

from app.risk.canonical import identity,require,seal
from app.risk.reasons import RiskFinding
from app.risk.vocabulary import RiskOutcome
from app.risk.trade_risk import TradeRiskEvaluation,TradeRiskStatus
from app.risk.portfolio_risk import PortfolioRiskEvaluation,PortfolioRiskStatus
from app.risk.risk_states import RiskStateEvaluation,RiskStateStatus
from app.risk.safety_gate import SafetyGateResult,SafetyGateStatus

INTEGRATED_RISK_VERSION="1.0.0"


class GateName(str,Enum):
    TRADE_RISK="TRADE_RISK"
    PORTFOLIO_RISK="PORTFOLIO_RISK"
    RISK_STATE="RISK_STATE"
    SAFETY_GATE="SAFETY_GATE"


MANDATORY_GATES=(GateName.TRADE_RISK,GateName.PORTFOLIO_RISK,GateName.RISK_STATE,GateName.SAFETY_GATE)


class GateDisposition(str,Enum):
    PASS="PASS"
    REJECTED="REJECTED"
    BLOCKED="BLOCKED"  # unavailable input, invalid result/context, or unmet prerequisite


RESULT_TYPES={GateName.TRADE_RISK:TradeRiskEvaluation,GateName.PORTFOLIO_RISK:PortfolioRiskEvaluation,
              GateName.RISK_STATE:RiskStateEvaluation,GateName.SAFETY_GATE:SafetyGateResult}
PASS_STATUSES={GateName.TRADE_RISK:TradeRiskStatus.TRADE_RISK_PASS,GateName.PORTFOLIO_RISK:PortfolioRiskStatus.PORTFOLIO_RISK_PASS,
               GateName.RISK_STATE:RiskStateStatus.RISK_STATE_PASS,GateName.SAFETY_GATE:SafetyGateStatus.SAFETY_GATE_PASS}


@dataclass(frozen=True,slots=True)
class GateFinding:
    gate: GateName
    gate_disposition: GateDisposition
    reason: RiskFinding
    source_result_identity: str | None
    blocking: bool=field(init=False)
    finding_identity: str=field(init=False)

    def __post_init__(self):
        require(self.gate,GateName,"gate")
        require(self.gate_disposition,GateDisposition,"gate disposition")
        require(self.reason,RiskFinding,"reason")
        if self.source_result_identity is not None: identity(self.source_result_identity,"result identity")
        object.__setattr__(self,"blocking",self.reason.code.outcome is RiskOutcome.REJECTED)
        seal(self,"integrated-gate-finding-1.0.0","finding_identity")


@dataclass(frozen=True,slots=True)
class GateRecord:
    gate: GateName
    disposition: GateDisposition
    result: TradeRiskEvaluation | PortfolioRiskEvaluation | RiskStateEvaluation | SafetyGateResult | None
    findings: tuple[GateFinding,...]
    record_identity: str=field(init=False)

    def __post_init__(self):
        require(self.gate,GateName,"gate")
        require(self.disposition,GateDisposition,"disposition")
        if self.result is not None: require(self.result,RESULT_TYPES[self.gate],"gate result")
        if not isinstance(self.findings,(tuple,list)) or not self.findings: raise ValueError("Explicit gate findings required")
        for row in self.findings:
            require(row,GateFinding,"gate finding")
            if row.gate is not self.gate or row.gate_disposition is not self.disposition:
                raise ValueError("Gate finding context mismatch")
            if row.source_result_identity != (self.result.evaluation_identity if self.result is not None else None):
                raise ValueError("Gate finding must bind the exact source result")
        if len({f.finding_identity for f in self.findings})!=len(self.findings): raise ValueError("Duplicate gate finding")
        object.__setattr__(self,"findings",tuple(sorted(self.findings,key=lambda f:(f.reason.code.value,f.finding_identity))))
        rejected=any(f.blocking for f in self.findings)
        if self.disposition is GateDisposition.PASS:
            if rejected or self.result is None or self.result.status is not PASS_STATUSES[self.gate]:
                raise ValueError("A gate pass requires the actual passing source result")
        elif not rejected:
            raise ValueError("Rejected/blocked gates require a blocking reason")
        if self.disposition is GateDisposition.REJECTED and (self.result is None or self.result.status is PASS_STATUSES[self.gate]):
            raise ValueError("Use BLOCKED when no underlying rejection was computed")
        seal(self,"integrated-gate-record-1.0.0","record_identity")
