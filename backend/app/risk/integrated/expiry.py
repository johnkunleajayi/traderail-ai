"""Approval validity intersection, including explicit freshness horizons."""
from dataclasses import dataclass,field
from datetime import datetime

from app.risk.canonical import identity,seal,timestamp


@dataclass(frozen=True,slots=True)
class ValidityBoundary:
    name: str
    valid_until: datetime
    evidence_identity: str

    def __post_init__(self):
        if not isinstance(self.name,str) or not self.name: raise ValueError("Named boundary required")
        identity(self.evidence_identity,"boundary identity")
        object.__setattr__(self,"valid_until",timestamp(self.valid_until))


@dataclass(frozen=True,slots=True)
class ApprovalValidity:
    evaluated_at: datetime
    boundaries: tuple[ValidityBoundary,...]
    expires_at: datetime=field(init=False)
    validity_identity: str=field(init=False)

    def __post_init__(self):
        object.__setattr__(self,"evaluated_at",timestamp(self.evaluated_at))
        if not isinstance(self.boundaries,(tuple,list)) or not self.boundaries: raise ValueError("Bounded evidence required")
        if any(type(b) is not ValidityBoundary for b in self.boundaries): raise ValueError("Typed boundaries required")
        if len({b.name for b in self.boundaries})!=len(self.boundaries): raise ValueError("Distinct boundary names required")
        object.__setattr__(self,"boundaries",tuple(sorted(self.boundaries,key=lambda b:b.name)))
        expiry=min(b.valid_until for b in self.boundaries)
        if expiry<=self.evaluated_at: raise ValueError("No positive safe approval lifetime remains")
        object.__setattr__(self,"expires_at",expiry)
        seal(self,"integrated-approval-validity-1.0.0","validity_identity")


def derive_approval_validity(i,safety):
    boundaries=[]
    def add(name,until,source): boundaries.append(ValidityBoundary(name,until,source))
    def evidence(name,row,expiry_name,maximum_age=None):
        source=getattr(row,"evidence_identity",None) or getattr(row,"context_identity",None) or row.inventory_identity
        add(name+"_VALIDITY",getattr(row,expiry_name),source)
        if maximum_age is not None: add(name+"_FRESHNESS",row.as_of+maximum_age,source)
    add("MAX_APPROVAL_AGE",i.evaluated_at+min(i.configuration.operating_limits.max_approval_age,
        i.configuration.platform_hard_limits.max_approval_age),i.configuration.configuration_identity)
    add("INTENT",i.intent.expires_at,i.intent.intent_identity)
    add("INTENT_MARKET",i.intent.market_context.expires_at,i.intent.market_context.context_identity)
    add("MARKET",i.market_context.expires_at,i.market_context.context_identity)
    add("SNAPSHOT",i.snapshot.expires_at,i.snapshot.snapshot_identity)
    add("ENTRY_MARKET",i.sizing_assumptions.entry_context.expires_at,i.sizing_assumptions.entry_context.context_identity)
    evidence("PORTFOLIO_CONTEXT",i.portfolio_context,"expires_at")
    for index,row in enumerate(i.portfolio_context.correlations):
        evidence("CORRELATION_"+str(index),row,"expires_at",i.portfolio_policy.operating_correlation_max_age)
    evidence("DRAWDOWN",i.drawdown_evidence,"expires_at",i.state_policy.max_evidence_age)
    evidence("SWITCH_INVENTORY",i.switch_inventory,"valid_until",i.safety_policy.max_inventory_age)
    for row in safety.applicable_switches:
        evidence("SWITCH_"+str(row.switch_id),row,"valid_until",i.safety_policy.max_switch_age)
    evidence("LIFECYCLE",i.lifecycle_evidence,"valid_until",i.safety_policy.max_lifecycle_age)
    evidence("HEALTH",i.health_evidence,"valid_until",i.safety_policy.max_health_age)
    add("HEALTH_DATA_FRESHNESS",i.health_evidence.data_through+i.safety_policy.max_health_data_age,i.health_evidence.evidence_identity)
    return ApprovalValidity(i.evaluated_at,tuple(boundaries))
