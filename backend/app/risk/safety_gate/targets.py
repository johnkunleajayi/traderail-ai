"""Exact declared targets; no symbol-name inference or venue assumptions."""
from dataclasses import dataclass, field
from uuid import UUID
import re

from app.market_intelligence.types import TIMEFRAME_SECONDS
from app.risk.canonical import identity, require, seal, symbol
from app.risk.vocabulary import KillSwitchScope
from app.strategies.configuration import StrategyDefinition


@dataclass(frozen=True, slots=True)
class StrategyTarget:
    family: str
    version: str
    configuration_identity: str | None
    target_identity: str = field(init=False)

    def __post_init__(self):
        StrategyDefinition(self.family, self.version, {})  # reuse canonical family/version validation
        if self.configuration_identity is not None:
            identity(self.configuration_identity, "strategy configuration")
        seal(self, "safety-strategy-target-1.0.0", "target_identity")

    def matches(self, strategy):
        return (self.family == strategy.family and self.version == strategy.version
                and (self.configuration_identity is None or self.configuration_identity == strategy.configuration_identity))


@dataclass(frozen=True, slots=True)
class InstrumentTarget:
    instrument_id: UUID
    canonical_symbol: str

    def __post_init__(self):
        require(self.instrument_id, UUID, "instrument_id")
        symbol(self.canonical_symbol)

    def matches(self, instrument):
        return self.instrument_id == instrument.instrument_id and self.canonical_symbol == instrument.canonical_symbol

    def conflicts_with(self, instrument):
        return (self.instrument_id == instrument.instrument_id) != (self.canonical_symbol == instrument.canonical_symbol)


@dataclass(frozen=True, slots=True)
class DataTarget:
    provider: str
    instrument: InstrumentTarget | None
    timeframe: str | None
    data_identity: str | None

    def __post_init__(self):
        if not isinstance(self.provider, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.provider):
            raise ValueError("An explicit canonical provider is required")
        if self.instrument is not None:
            require(self.instrument, InstrumentTarget, "instrument")
        if self.timeframe is not None and self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported canonical timeframe")
        if self.data_identity is not None:
            identity(self.data_identity, "data identity")

    def matches(self, intent):
        return (self.provider == intent.instrument.provider
            and (self.instrument is None or self.instrument.matches(intent.instrument))
            and (self.timeframe is None or self.timeframe == intent.instrument.timeframe)
            and (self.data_identity is None or self.data_identity == intent.market_context.data_identity))


@dataclass(frozen=True, slots=True)
class KillSwitchTarget:
    scope: KillSwitchScope
    user_id: UUID | None = None
    portfolio_id: UUID | None = None
    strategy: StrategyTarget | None = None
    instrument: InstrumentTarget | None = None
    data: DataTarget | None = None
    target_identity: str = field(init=False)

    def __post_init__(self):
        require(self.scope, KillSwitchScope, "switch scope")
        fields = {"user_id":UUID, "portfolio_id":UUID, "strategy":StrategyTarget,
                  "instrument":InstrumentTarget, "data":DataTarget}
        expected = {KillSwitchScope.USER:"user_id", KillSwitchScope.PORTFOLIO:"portfolio_id",
            KillSwitchScope.STRATEGY:"strategy", KillSwitchScope.MARKET:"instrument", KillSwitchScope.DATA:"data"}.get(self.scope)
        for name, kind in fields.items():
            if name == expected:
                require(getattr(self, name), kind, name)
            elif getattr(self, name) is not None:
                raise ValueError("A switch may carry only its explicitly declared target")
        seal(self, "kill-switch-target-1.0.0", "target_identity")

    def conflicts_with(self, intent):
        if self.scope is KillSwitchScope.STRATEGY:
            return (self.strategy.configuration_identity == intent.strategy.configuration_identity
                    and (self.strategy.family != intent.strategy.family or self.strategy.version != intent.strategy.version))
        if self.scope is KillSwitchScope.MARKET:
            return self.instrument.conflicts_with(intent.instrument)
        if self.scope is KillSwitchScope.DATA and self.data.provider == intent.instrument.provider and self.data.instrument is not None:
            return self.data.instrument.conflicts_with(intent.instrument)
        return False

    def matches(self, intent):
        if self.scope is KillSwitchScope.USER:
            return self.user_id == intent.scope.user_id
        if self.scope is KillSwitchScope.PORTFOLIO:
            return self.portfolio_id == intent.scope.portfolio_id
        if self.scope is KillSwitchScope.STRATEGY:
            return self.strategy.matches(intent.strategy)
        if self.scope is KillSwitchScope.MARKET:
            return self.instrument.matches(intent.instrument)
        if self.scope is KillSwitchScope.DATA:
            return self.data.matches(intent)
        return self.scope is KillSwitchScope.SYSTEM
