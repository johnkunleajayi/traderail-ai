"""Scoped immutable evidence only. No final RiskDecision or execution authority."""
from dataclasses import dataclass,field
from datetime import datetime
from enum import Enum

from app.risk.canonical import identity,require,seal,timestamp
from app.risk.reasons import RiskCategory,RiskFinding,RiskOutcome
from .health import StrategyHealth,StrategyHealthEvidence,StrategyLifecycleEvidence
from .kill_switches import KillSwitchEvidence

SAFETY_GATE_VERSION="1.0.0"


class KillSwitchStatus(str,Enum):
    KILL_SWITCH_PASS="KILL_SWITCH_PASS"
    KILL_SWITCH_REJECTED="KILL_SWITCH_REJECTED"


class SafetyGateStatus(str,Enum):
    SAFETY_GATE_PASS="SAFETY_GATE_PASS"
    SAFETY_GATE_REJECTED="SAFETY_GATE_REJECTED"


@dataclass(frozen=True,slots=True)
class SafetyGateResult:
    intent_identity: str | None
    configuration_identity: str | None
    policy_identity: str | None
    inventory_identity: str | None
    lifecycle_identity: str | None
    health_identity: str | None
    evaluated_at: datetime
    kill_switch_status: KillSwitchStatus
    applicable_switches: tuple[KillSwitchEvidence,...]
    active_blocking_switches: tuple[KillSwitchEvidence,...]
    lifecycle_eligible: bool | None
    lifecycle_evidence: StrategyLifecycleEvidence | None
    health_evidence: StrategyHealthEvidence | None
    findings: tuple[RiskFinding,...]
    status: SafetyGateStatus
    scope: str=field(default="KILL_SWITCH_AND_STRATEGY_HEALTH_ONLY",init=False)
    version: str=field(default=SAFETY_GATE_VERSION,init=False)
    evaluation_identity: str=field(init=False)

    def __post_init__(self):
        ids=("intent_identity","configuration_identity","policy_identity","inventory_identity","lifecycle_identity","health_identity")
        for name in ids:
            if getattr(self,name) is not None: identity(getattr(self,name),name)
        object.__setattr__(self,"evaluated_at",timestamp(self.evaluated_at))
        require(self.kill_switch_status,KillSwitchStatus,"kill-switch status")
        require(self.status,SafetyGateStatus,"status")
        if self.lifecycle_eligible is not None: require(self.lifecycle_eligible,bool,"lifecycle eligibility")
        for name,kind,id_name in (("health_evidence",StrategyHealthEvidence,"health_identity"),
                                  ("lifecycle_evidence",StrategyLifecycleEvidence,"lifecycle_identity")):
            row=getattr(self,name)
            if row is not None:
                require(row,kind,name)
                if row.evidence_identity!=getattr(self,id_name): raise ValueError("Strategy evidence identity mismatch")
            elif getattr(self,id_name) is not None: raise ValueError("Missing referenced strategy evidence")
        for name in ("applicable_switches","active_blocking_switches"):
            rows=getattr(self,name)
            if not isinstance(rows,(tuple,list)): raise ValueError("Explicit switch sequence required")
            for row in rows: require(row,KillSwitchEvidence,"switch")
            if len({row.switch_id for row in rows})!=len(rows): raise ValueError("Duplicate switch evidence")
            object.__setattr__(self,name,tuple(sorted(rows,key=lambda row:str(row.switch_id))))
        if any(row not in self.applicable_switches or row.active is not True or row.as_of>self.evaluated_at
               or row.activated_at>self.evaluated_at or (row.expires_at is not None and row.expires_at<=self.evaluated_at)
               for row in self.active_blocking_switches): raise ValueError("Blocking switches must be applicable observed active switches")
        if not isinstance(self.findings,(tuple,list)) or not self.findings: raise ValueError("Typed findings required")
        for row in self.findings: require(row,RiskFinding,"finding")
        if len({row.code for row in self.findings})!=len(self.findings): raise ValueError("Findings must be grouped by code")
        object.__setattr__(self,"findings",tuple(sorted(self.findings,key=lambda row:row.code.value)))
        rejected=any(row.code.outcome is RiskOutcome.REJECTED for row in self.findings)
        if (self.status is SafetyGateStatus.SAFETY_GATE_REJECTED)!=rejected: raise ValueError("Outcome must match all typed findings")
        kill_rejected=any(row.code.category is RiskCategory.KILL_SWITCH and row.code.outcome is RiskOutcome.REJECTED for row in self.findings)
        if self.kill_switch_status is KillSwitchStatus.KILL_SWITCH_PASS and (kill_rejected or self.active_blocking_switches or self.inventory_identity is None):
            raise ValueError("A kill-switch pass requires complete non-blocking evidence")
        if self.status is SafetyGateStatus.SAFETY_GATE_PASS:
            if (any(getattr(self,name) is None for name in ids) or self.lifecycle_eligible is not True
                or self.health_evidence is None or self.health_evidence.state not in (StrategyHealth.HEALTHY,StrategyHealth.DEGRADED)
                or self.kill_switch_status is not KillSwitchStatus.KILL_SWITCH_PASS): raise ValueError("Incomplete evidence cannot pass")
        seal(self,"safety-evaluation-1.0.0","evaluation_identity")
