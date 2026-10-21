"""Explicit limits, with no live presets and no silent clamping."""
from dataclasses import dataclass, field, fields
from datetime import timedelta
from decimal import Decimal

from .canonical import decimal, require, seal, text
from .vocabulary import Environment, RiskProfile


@dataclass(frozen=True, slots=True)
class _Limits:
    max_trade_risk_fraction: Decimal
    max_open_risk_fraction: Decimal
    max_gross_exposure_fraction: Decimal
    max_directional_exposure_fraction: Decimal
    max_instrument_concentration_fraction: Decimal
    max_strategy_concentration_fraction: Decimal
    max_correlated_exposure_fraction: Decimal
    max_drawdown_fraction: Decimal
    max_positions: int
    max_approval_age: timedelta
    limits_identity: str = field(init=False)

    def __post_init__(self):
        for item in fields(self):
            if item.name.endswith("_fraction"):
                decimal(getattr(self, item.name), item.name, fraction=True)
        require(self.max_positions, int, "max_positions")
        require(self.max_approval_age, timedelta, "max_approval_age")
        if self.max_positions < 0 or self.max_approval_age <= timedelta(0):
            raise ValueError("Position cap must be nonnegative; approval age must be positive")
        seal(self, type(self).__name__, "limits_identity")


@dataclass(frozen=True, slots=True)
class OperatingLimits(_Limits):
    """User/profile operating ceilings, never greater than platform ceilings."""


@dataclass(frozen=True, slots=True)
class PlatformHardLimits(_Limits):
    """Platform-owned immutable ceilings; not a user override."""


@dataclass(frozen=True, slots=True)
class RiskConfiguration:
    version: str
    environment: Environment
    profile: RiskProfile
    operating_limits: OperatingLimits
    platform_hard_limits: PlatformHardLimits
    configuration_identity: str = field(init=False)

    def __post_init__(self):
        text(self.version, "version")
        require(self.environment, Environment, "environment")
        require(self.profile, RiskProfile, "profile")
        require(self.operating_limits, OperatingLimits, "operating_limits")
        require(self.platform_hard_limits, PlatformHardLimits, "platform_hard_limits")
        for item in fields(self.operating_limits):
            if item.init and getattr(self.operating_limits, item.name) > getattr(self.platform_hard_limits, item.name):
                raise ValueError("Operating limits cannot exceed platform hard limits: " + item.name)
        seal(self, "configuration", "configuration_identity")
