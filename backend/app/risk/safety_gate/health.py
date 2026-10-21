"""Health and lifecycle are separate trusted evidence, never profit predictions."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re

from app.risk.canonical import decimal, identity, require, seal, timestamp
from app.risk.vocabulary import Environment
from app.strategies.configuration import StrategyDefinition
from app.strategy_lab.lifecycle import StrategyLifecycle
from app.strategy_lab.numeric import require_decimal
from app.strategy_lab.validation_evidence.models import Verdict


class StrategyHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class HealthMetrics:
    completed_trade_count: int | None
    expectancy: Decimal | None
    expectancy_currency: str | None
    profit_factor: Decimal | None
    drawdown_fraction: Decimal | None
    stress_degradation: Decimal | None
    data_sufficient: bool | None
    validation_verdict: Verdict | None
    validation_report_identity: str | None
    metrics_identity: str = field(init=False)

    def __post_init__(self):
        if self.completed_trade_count is not None:
            require(self.completed_trade_count, int, "completed trade count")
            if self.completed_trade_count < 0:
                raise ValueError("Trade count must be nonnegative")
        if self.expectancy_currency is not None and (not isinstance(self.expectancy_currency,str)
                or not re.fullmatch(r"[A-Z][A-Z0-9]{0,15}",self.expectancy_currency)):
            raise ValueError("Explicit canonical expectancy currency or unknown None is required")
        if self.expectancy is not None:
            require_decimal(self.expectancy, "expectancy")  # negative observations are meaningful
        for name in ("profit_factor", "drawdown_fraction", "stress_degradation"):
            if getattr(self, name) is not None:
                decimal(getattr(self,name),name,fraction=name=="drawdown_fraction")
        if self.data_sufficient is not None:
            require(self.data_sufficient,bool,"data sufficiency")
        if self.validation_verdict is not None:
            require(self.validation_verdict,Verdict,"research verdict")
        if self.validation_report_identity is not None:
            identity(self.validation_report_identity,"validation report")
        if (self.validation_verdict is None) != (self.validation_report_identity is None):
            raise ValueError("Validation verdict and report identity must be supplied together")
        seal(self,"health-metrics-1.0.0","metrics_identity")


@dataclass(frozen=True, slots=True)
class StrategyHealthEvidence:
    strategy: StrategyDefinition
    environment: Environment
    state: StrategyHealth
    metrics: HealthMetrics | None
    window_start: datetime
    data_through: datetime
    as_of: datetime
    valid_until: datetime
    source_identity: str
    evidence_identity: str = field(init=False)

    def __post_init__(self):
        require(self.strategy,StrategyDefinition,"strategy")
        require(self.environment,Environment,"environment")
        require(self.state,StrategyHealth,"health state")
        if self.metrics is not None:
            require(self.metrics,HealthMetrics,"metrics")
        identity(self.source_identity,"source identity")
        for name in ("window_start","data_through","as_of","valid_until"):
            object.__setattr__(self,name,timestamp(getattr(self,name)))
        if not self.window_start < self.data_through <= self.as_of < self.valid_until:
            raise ValueError("Health observations require a causal declared window and bounded validity")
        seal(self,"strategy-health-evidence-1.0.0","evidence_identity")


@dataclass(frozen=True, slots=True)
class StrategyLifecycleEvidence:
    strategy: StrategyDefinition
    environment: Environment
    lifecycle: StrategyLifecycle
    as_of: datetime
    valid_until: datetime
    source_identity: str
    evidence_identity: str = field(init=False)

    def __post_init__(self):
        require(self.strategy,StrategyDefinition,"strategy")
        require(self.environment,Environment,"environment")
        require(self.lifecycle,StrategyLifecycle,"lifecycle")
        identity(self.source_identity,"source identity")
        for name in ("as_of","valid_until"):
            object.__setattr__(self,name,timestamp(getattr(self,name)))
        if self.as_of >= self.valid_until:
            raise ValueError("Lifecycle evidence requires bounded validity")
        seal(self,"strategy-lifecycle-evidence-1.0.0","evidence_identity")
