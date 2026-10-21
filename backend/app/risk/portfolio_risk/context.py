"""Trusted proposal-specific attribution, valuation and group declarations."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import re

from app.risk.canonical import decimal, identity, require, seal, symbol, timestamp
from .correlation import CorrelationEvidence


def sequence(value, kind, name, key):
    if not isinstance(value, (tuple, list)):
        raise ValueError(name + " requires an explicit sequence, including an explicit empty tuple")
    rows = tuple(value)
    for row in rows:
        require(row, kind, name)
    if len({key(row) for row in rows}) != len(rows):
        raise ValueError("Duplicate " + name)
    return tuple(sorted(rows, key=key))


@dataclass(frozen=True, slots=True)
class StrategyAttribution:
    configuration_identity: str
    family: str
    version: str
    source_identity: str
    strategy_identity: str = field(init=False)
    attribution_identity: str = field(init=False)

    def __post_init__(self):
        identity(self.configuration_identity, "configuration_identity")
        identity(self.source_identity, "source_identity")
        if not isinstance(self.family, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.family):
            raise ValueError("Explicit strategy family required")
        if not isinstance(self.version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", self.version):
            raise ValueError("Explicit strategy version required")
        from app.strategy_lab.canonical import canonical_json, fingerprint
        object.__setattr__(self, "strategy_identity", fingerprint("risk.strategy-family-version/v1",
            canonical_json({"family": self.family, "version": self.version})))
        seal(self, "strategy-attribution-1.0.0", "attribution_identity")


@dataclass(frozen=True, slots=True)
class ExposureGroup:
    code: str
    members: tuple[str, ...]
    platform_max_fraction: Decimal
    operating_max_fraction: Decimal
    source_identity: str
    group_identity: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.code):
            raise ValueError("Explicit group identifier required")
        if not isinstance(self.members, (tuple, list)) or not self.members:
            raise ValueError("An explicit nonempty group membership is required")
        for member in self.members:
            symbol(member)
        if len(set(self.members)) != len(self.members):
            raise ValueError("Duplicate group member")
        object.__setattr__(self, "members", tuple(sorted(self.members)))
        decimal(self.platform_max_fraction, "platform_max_fraction", fraction=True)
        decimal(self.operating_max_fraction, "operating_max_fraction", fraction=True)
        if self.operating_max_fraction > self.platform_max_fraction:
            raise ValueError("Operating group limit cannot exceed platform hard limit")
        identity(self.source_identity, "source_identity")
        seal(self, "exposure-group-1.0.0", "group_identity")


@dataclass(frozen=True, slots=True)
class PortfolioRiskContext:
    intent_identity: str
    snapshot_identity: str
    policy_identity: str
    valuation_currency: str
    valuation_identity: str
    as_of: datetime
    expires_at: datetime
    source_identity: str
    strategy_attributions: tuple[StrategyAttribution, ...]
    correlations: tuple[CorrelationEvidence, ...]
    groups: tuple[ExposureGroup, ...]
    context_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("intent_identity", "snapshot_identity", "policy_identity", "valuation_identity", "source_identity"):
            identity(getattr(self, name), name)
        if not isinstance(self.valuation_currency, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,15}", self.valuation_currency):
            raise ValueError("Explicit valuation currency required")
        for name in ("as_of", "expires_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if self.as_of >= self.expires_at:
            raise ValueError("Context requires bounded validity")
        for name, kind, key in (("strategy_attributions", StrategyAttribution, lambda r: r.configuration_identity),
                                ("correlations", CorrelationEvidence, lambda r: r.pair),
                                ("groups", ExposureGroup, lambda r: r.code)):
            object.__setattr__(self, name, sequence(getattr(self, name), kind, name, key))
        if self.correlations:
            window = self.correlations[0].observation_context
            if any(row.observation_context != window for row in self.correlations):
                raise ValueError("Correlation evidence must share an explicit observation context/window")
            if any(row.as_of > self.as_of for row in self.correlations):
                raise ValueError("Context cannot precede supplied correlation observations")
        seal(self, "portfolio-context-1.0.0", "context_identity")
