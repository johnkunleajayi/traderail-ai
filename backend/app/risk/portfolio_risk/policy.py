"""Explicit trusted platform/operating policy; no production risk presets."""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from app.risk.canonical import decimal, require, seal, text


@dataclass(frozen=True, slots=True)
class PortfolioRiskPolicy:
    version: str
    platform_correlation_threshold: Decimal
    operating_correlation_threshold: Decimal
    platform_correlation_max_age: timedelta
    operating_correlation_max_age: timedelta
    policy_identity: str = field(init=False)

    def __post_init__(self):
        text(self.version, "version")
        for name in ("platform_correlation_threshold", "operating_correlation_threshold"):
            decimal(getattr(self, name), name, fraction=True)
        for name in ("platform_correlation_max_age", "operating_correlation_max_age"):
            require(getattr(self, name), timedelta, name)
            if getattr(self, name) <= timedelta(0):
                raise ValueError("Correlation maximum age must be positive")
        if self.operating_correlation_threshold > self.platform_correlation_threshold:
            raise ValueError("An operating correlation threshold may only be lower/stricter")
        if self.operating_correlation_max_age > self.platform_correlation_max_age:
            raise ValueError("Operating correlation freshness cannot exceed the platform ceiling")
        seal(self, "portfolio-policy-1.0.0", "policy_identity")
