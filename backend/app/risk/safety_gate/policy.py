"""Explicit platform-owned eligibility and evidence requirements; no numeric presets."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from types import MappingProxyType
import re

from app.risk.canonical import decimal, require, seal, text
from app.risk.vocabulary import Environment
from app.strategy_lab.lifecycle import StrategyState
from app.strategy_lab.numeric import require_decimal
from app.strategy_lab.validation_evidence.models import Verdict
from .health import StrategyHealth


def ordered(values,kind,name,allow_empty=True):
    if not isinstance(values,(tuple,list)) or (not allow_empty and not values):
        raise ValueError(name+" requires an explicit sequence")
    for value in values:
        require(value,kind,name)
    if len(set(values)) != len(values):
        raise ValueError("Duplicate "+name)
    return tuple(sorted(values,key=lambda value:value.value))


@dataclass(frozen=True, slots=True)
class SafetyGatePolicy:
    version: str
    lifecycle_by_environment: Mapping[Environment, tuple[StrategyState,...]]
    permitted_health_states: tuple[StrategyHealth,...]
    max_inventory_age: timedelta
    max_switch_age: timedelta
    max_health_age: timedelta
    max_health_data_age: timedelta
    max_lifecycle_age: timedelta
    minimum_completed_trades: int | None
    minimum_expectancy: Decimal | None
    expectancy_currency: str | None
    minimum_profit_factor: Decimal | None
    maximum_drawdown_fraction: Decimal | None
    maximum_stress_degradation: Decimal | None
    require_data_sufficient: bool
    required_validation_verdicts: tuple[Verdict,...] | None
    policy_identity: str = field(init=False)

    def __post_init__(self):
        text(self.version,"version")
        if not isinstance(self.lifecycle_by_environment,Mapping) or set(self.lifecycle_by_environment)!=set(Environment):
            raise ValueError("All environments need an explicit lifecycle policy, including explicit deny-all tuples")
        for key in self.lifecycle_by_environment:
            require(key,Environment,"environment key")
        mapping={}
        for env in sorted(Environment,key=lambda value:value.value):
            require(env,Environment,"environment")
            states=ordered(self.lifecycle_by_environment[env],StrategyState,"lifecycle states")
            if any(state in (StrategyState.SUSPENDED,StrategyState.RETIRED) for state in states):
                raise ValueError("Suspended/retired lifecycle cannot receive new exposure")
            if env in (Environment.SHADOW,Environment.LIVE):
                envelope=((StrategyState.SHADOW,StrategyState.LIMITED_LIVE,StrategyState.LIVE) if env is Environment.SHADOW
                          else (StrategyState.LIMITED_LIVE,StrategyState.LIVE))
                if any(state not in envelope for state in states):
                    raise ValueError("Research lifecycle cannot acquire shadow/live eligibility through policy")
            mapping[env]=states
        object.__setattr__(self,"lifecycle_by_environment",MappingProxyType(mapping))
        states=ordered(self.permitted_health_states,StrategyHealth,"permitted health states")
        if any(state not in (StrategyHealth.HEALTHY,StrategyHealth.DEGRADED) for state in states):
            raise ValueError("Unhealthy, suspended and unknown health cannot be permitted")
        object.__setattr__(self,"permitted_health_states",states)
        for name in ("max_inventory_age","max_switch_age","max_health_age","max_health_data_age","max_lifecycle_age"):
            require(getattr(self,name),timedelta,name)
            if getattr(self,name)<=timedelta(0):
                raise ValueError("Evidence maximum ages must be positive")
        if self.minimum_completed_trades is not None:
            require(self.minimum_completed_trades,int,"minimum completed trades")
            if self.minimum_completed_trades<0:
                raise ValueError("Negative minimum trade count")
        if self.expectancy_currency is not None and (not isinstance(self.expectancy_currency,str)
                or not re.fullmatch(r"[A-Z][A-Z0-9]{0,15}",self.expectancy_currency)):
            raise ValueError("A canonical expectancy threshold currency is required")
        if self.minimum_expectancy is not None and self.expectancy_currency is None:
            raise ValueError("A monetary expectancy threshold requires explicit currency")
        if self.minimum_expectancy is not None:
            require_decimal(self.minimum_expectancy,"minimum expectancy")
        for name in ("minimum_profit_factor","maximum_drawdown_fraction","maximum_stress_degradation"):
            if getattr(self,name) is not None:
                decimal(getattr(self,name),name,fraction=name=="maximum_drawdown_fraction")
        require(self.require_data_sufficient,bool,"data-sufficiency policy")
        if self.required_validation_verdicts is not None:
            object.__setattr__(self,"required_validation_verdicts",ordered(self.required_validation_verdicts,Verdict,"research verdicts",False))
        seal(self,"safety-policy-1.0.0","policy_identity")
