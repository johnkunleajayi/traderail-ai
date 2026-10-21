"""Platform-owned explicit hypotheses. No production presets or user overrides."""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from app.risk.canonical import decimal, require, seal, text
from app.risk.vocabulary import RiskState

STATES = (RiskState.NORMAL, RiskState.CAUTION, RiskState.DEFENSIVE, RiskState.SUSPENDED)


@dataclass(frozen=True, slots=True)
class RiskStatePolicy:
    version: str
    caution_entry: Decimal
    defensive_entry: Decimal
    suspended_entry: Decimal
    caution_recovery: Decimal
    defensive_recovery: Decimal
    suspended_recovery: Decimal
    normal_multiplier: Decimal
    caution_multiplier: Decimal
    defensive_multiplier: Decimal
    suspended_multiplier: Decimal
    max_evidence_age: timedelta
    policy_identity: str = field(init=False)

    def __post_init__(self):
        text(self.version, "version")
        names = ("caution", "defensive", "suspended")
        for name in names:
            decimal(getattr(self, name + "_entry"), name + " entry", positive=True, fraction=True)
            decimal(getattr(self, name + "_recovery"), name + " recovery", fraction=True)
            if getattr(self, name + "_recovery") >= getattr(self, name + "_entry"):
                raise ValueError("Recovery must be strictly below its entry threshold")
        if not self.caution_entry < self.defensive_entry < self.suspended_entry:
            raise ValueError("Entry thresholds must strictly increase with severity")
        if not self.caution_recovery < self.defensive_recovery < self.suspended_recovery:
            raise ValueError("Recovery thresholds must strictly increase with severity")
        multipliers = tuple(getattr(self, state.value.lower() + "_multiplier") for state in STATES)
        for value in multipliers:
            decimal(value, "state multiplier", fraction=True)
        if any(a < b for a, b in zip(multipliers, multipliers[1:])) or self.suspended_multiplier != Decimal("0"):
            raise ValueError("Severity cannot increase risk; SUSPENDED multiplier must equal zero")
        require(self.max_evidence_age, timedelta, "maximum evidence age")
        if self.max_evidence_age <= timedelta(0):
            raise ValueError("Maximum evidence age must be positive")
        seal(self, "state-policy-1.0.0", "policy_identity")

    def multiplier(self, state):
        require(state, RiskState, "state")
        return getattr(self, state.value.lower() + "_multiplier")
