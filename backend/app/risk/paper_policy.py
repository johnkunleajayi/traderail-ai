"""Explicit product-approved PAPER policy; no HTTP or environment overrides.

Every authoritative PAPER portfolio scope selects the same BALANCED policy.
This is configuration only, not approval, evidence construction or execution.
"""
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from types import MappingProxyType
from .canonical import require
from .configuration import RiskConfiguration,OperatingLimits,PlatformHardLimits
from .context import PortfolioScope
from .vocabulary import Environment,RiskProfile

PAPER_POLICY_VERSION = 'paper-risk-v1'

PAPER_RISK_V1 = RiskConfiguration(
    version=PAPER_POLICY_VERSION,
    environment=Environment.PAPER,
    profile=RiskProfile.BALANCED,
    platform_hard_limits=PlatformHardLimits(
        max_trade_risk_fraction=Decimal('0.02'),
        max_open_risk_fraction=Decimal('0.06'),
        max_gross_exposure_fraction=Decimal('0.50'),
        max_directional_exposure_fraction=Decimal('0.40'),
        max_instrument_concentration_fraction=Decimal('0.25'),
        max_strategy_concentration_fraction=Decimal('0.35'),
        max_correlated_exposure_fraction=Decimal('0.35'),
        max_drawdown_fraction=Decimal('0.10'),
        max_positions=4,
        max_approval_age=timedelta(minutes=10)),
    operating_limits=OperatingLimits(
        max_trade_risk_fraction=Decimal('0.01'),
        max_open_risk_fraction=Decimal('0.03'),
        max_gross_exposure_fraction=Decimal('0.30'),
        max_directional_exposure_fraction=Decimal('0.25'),
        max_instrument_concentration_fraction=Decimal('0.15'),
        max_strategy_concentration_fraction=Decimal('0.20'),
        max_correlated_exposure_fraction=Decimal('0.20'),
        max_drawdown_fraction=Decimal('0.06'),
        max_positions=3,
        max_approval_age=timedelta(minutes=5)))

_POLICIES = MappingProxyType({PAPER_POLICY_VERSION:PAPER_RISK_V1})


class PaperRiskPolicyUnavailable(ValueError):
    """No explicitly configured PAPER policy exists for this selection."""


def configuration_for_paper_portfolio(scope: PortfolioScope, *, version=PAPER_POLICY_VERSION):
    """Called with server-derived ownership/scope, never client financial state.

    No user/profile/limits argument exists. This function neither establishes
    portfolio ownership nor constructs the other mandatory trusted Risk inputs.
    """
    require(scope,PortfolioScope,'authoritative portfolio scope')
    if scope.environment is not Environment.PAPER:
        raise PaperRiskPolicyUnavailable('Only PAPER has a configured risk policy')
    if type(version) is not str or not isinstance(_POLICIES,Mapping):
        raise PaperRiskPolicyUnavailable('PAPER risk policy is unavailable')
    policy=_POLICIES.get(version)
    if (type(policy) is not RiskConfiguration or policy.version!=version
            or policy.environment is not Environment.PAPER or policy.profile is not RiskProfile.BALANCED):
        raise PaperRiskPolicyUnavailable('PAPER risk policy is unavailable')
    return policy
