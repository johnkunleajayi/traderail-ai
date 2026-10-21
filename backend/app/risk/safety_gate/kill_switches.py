"""Trusted switch observations and complete inventory attestation; no persistence."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID

from app.risk.canonical import identity, require, seal, timestamp
from app.risk.context import PortfolioScope
from app.risk.vocabulary import Environment, KillSwitchScope
from .targets import KillSwitchTarget


class SwitchCause(str, Enum):
    MANUAL_CONTROL = "MANUAL_CONTROL"
    DATA_INTEGRITY = "DATA_INTEGRITY"
    STRATEGY_CONTROL = "STRATEGY_CONTROL"
    OPERATIONS = "OPERATIONS"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True, slots=True)
class KillSwitchEvidence:
    switch_id: UUID
    target: KillSwitchTarget
    environment: Environment
    active: bool | None
    cause: SwitchCause
    activated_at: datetime | None
    expires_at: datetime | None  # operational switch expiry, not evidence validity
    as_of: datetime
    valid_until: datetime
    source_identity: str
    evidence_identity: str = field(init=False)

    def __post_init__(self):
        require(self.switch_id, UUID, "switch_id")
        require(self.target, KillSwitchTarget, "target")
        require(self.environment, Environment, "environment")
        require(self.cause, SwitchCause, "switch cause")
        identity(self.source_identity, "source identity")
        if self.active is not None:
            require(self.active, bool, "active state")
        for name in ("as_of", "valid_until"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        for name in ("activated_at", "expires_at"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, timestamp(getattr(self, name)))
        if self.as_of >= self.valid_until:
            raise ValueError("Switch evidence requires bounded validity")
        if self.active is True and self.activated_at is None:
            raise ValueError("An active switch requires an activation timestamp")
        if self.activated_at is not None and self.activated_at > self.as_of:
            raise ValueError("Activation cannot follow its observation")
        if self.expires_at is not None and (self.activated_at is None or self.expires_at <= self.activated_at):
            raise ValueError("Operational expiry must follow activation")
        seal(self, "kill-switch-evidence-1.0.0", "evidence_identity")


@dataclass(frozen=True, slots=True)
class KillSwitchInventory:
    intent_identity: str
    scope: PortfolioScope
    market_context_identity: str
    covered_scopes: tuple[KillSwitchScope, ...]
    switches: tuple[KillSwitchEvidence, ...]
    as_of: datetime
    valid_until: datetime
    source_identity: str
    inventory_identity: str = field(init=False)

    def __post_init__(self):
        require(self.scope, PortfolioScope, "scope")
        for name in ("intent_identity", "market_context_identity", "source_identity"):
            identity(getattr(self, name), name)
        if not isinstance(self.covered_scopes, (tuple,list)) or not isinstance(self.switches, (tuple,list)):
            raise ValueError("Explicit coverage and switch sequences are required")
        for value in self.covered_scopes:
            require(value, KillSwitchScope, "covered scope")
        if len(set(self.covered_scopes)) != len(self.covered_scopes):
            raise ValueError("Duplicate scope coverage")
        for row in self.switches:
            require(row, KillSwitchEvidence, "switch evidence")
        if len({row.switch_id for row in self.switches}) != len(self.switches):
            raise ValueError("Duplicate/conflicting observations for one switch")
        for name in ("as_of", "valid_until"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if self.as_of >= self.valid_until or any(row.as_of > self.as_of for row in self.switches):
            raise ValueError("Inventory must follow all included observations and have bounded validity")
        object.__setattr__(self, "covered_scopes",tuple(sorted(self.covered_scopes,key=lambda x:x.value)))
        object.__setattr__(self, "switches",tuple(sorted(self.switches,key=lambda x:str(x.switch_id))))
        seal(self, "kill-switch-inventory-1.0.0", "inventory_identity")
