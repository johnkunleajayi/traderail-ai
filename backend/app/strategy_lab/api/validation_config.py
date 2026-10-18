"""API-owned research probes, never parameter search or venue fee claims."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from app.strategies.adaptive_trend.config import DEFAULT_CONFIGURATION
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.numeric import exact_sum
from app.strategy_lab.stress_testing import StressScenario

VALIDATION_API_VERSION = "1.0.0"
MAX_VALIDATION_FOLDS = 4
SENSITIVITY_CONFIGURATIONS = (DEFAULT_CONFIGURATION,
    replace(DEFAULT_CONFIGURATION,entry_min_ema_spread=Decimal(".0016")),
    replace(DEFAULT_CONFIGURATION,entry_fast_slope=Decimal(".00021")))
FEE_INCREMENT = Decimal(".001")
SPREAD_INCREMENT = Decimal(".002")
SLIPPAGE_INCREMENT = Decimal(".001")
CAPITAL_FAILURE_MESSAGES = frozenset((
    "Insufficient unleveraged capital; quantity will not be resized",
    "Insufficient capital to settle short cover without borrowing",
    "Short collateral exceeds unleveraged one-times-equity exposure limit",
    "Negative cash/equity would require unsupported borrowing"))


def stress_scenarios(baseline: ResearchCostModel, duration: timedelta):
    fee=exact_sum((baseline.trading_fee_rate,FEE_INCREMENT))
    spread=exact_sum((baseline.spread_rate,SPREAD_INCREMENT))
    slippage=exact_sum((baseline.slippage_rate,SLIPPAGE_INCREMENT))
    return (StressScenario("BASELINE",baseline),
        StressScenario("HIGHER_FEE",replace(baseline,trading_fee_rate=fee)),
        StressScenario("WIDER_SPREAD",replace(baseline,spread_rate=spread)),
        StressScenario("HIGHER_SLIPPAGE",replace(baseline,slippage_rate=slippage)),
        StressScenario("ADDITIONAL_DELAY",replace(baseline,execution_delay=baseline.execution_delay+duration)),
        StressScenario("COMBINED_ADVERSE_COST",replace(baseline,trading_fee_rate=fee,spread_rate=spread,slippage_rate=slippage)))
