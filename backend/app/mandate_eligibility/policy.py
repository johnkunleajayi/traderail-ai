"""Versioned server-owned product eligibility, never signals or authorization."""
from dataclasses import asdict
from app.risk.paper_policy import PAPER_RISK_V1
from app.strategies.configuration import StrategyDefinition
from app.strategies.adaptive_trend.config import DEFAULT_CONFIGURATION, STRATEGY_FAMILY, STRATEGY_VERSION
from .contracts import EligibilityPolicy, RiskConfigurationReference, TechnicalConfiguration, TechnicalMapping

# Historical policy semantics remain unchanged and independently resolvable.
POLICY_V1 = EligibilityPolicy(
    version="mandate-eligibility-v1",
    risk_references=(RiskConfigurationReference(preference="BALANCED", version=PAPER_RISK_V1.version,
                    configuration_identity=PAPER_RISK_V1.configuration_identity),),
    technical_mappings=(),
)

# Reuse exactly the canonical definition built by AdaptiveTrendStrategy, without
# constructing observations, evaluating the strategy or copying parameter values.
_definition = StrategyDefinition(STRATEGY_FAMILY, STRATEGY_VERSION, asdict(DEFAULT_CONFIGURATION))
ADAPTIVE_TREND_REFERENCE = TechnicalConfiguration(
    strategy_family=_definition.family,
    strategy_version=_definition.version,
    configuration_identity=_definition.configuration_identity,
    timeframe="1h",
)
POLICY_V2 = EligibilityPolicy(
    version="mandate-eligibility-v2",
    risk_references=POLICY_V1.risk_references,
    technical_mappings=(TechnicalMapping(
        environment="PAPER", instrument="BTC/USDT", allocation_currency="USDT",
        direction_capability="LONG_ONLY", trading_style="MEDIUM_TERM", risk_level="BALANCED",
        configurations=(ADAPTIVE_TREND_REFERENCE,),
    ),),
)
POLICY = POLICY_V2
