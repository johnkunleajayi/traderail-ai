"""Pure Decimal drawdown and threshold comparisons, independent of ambient precision."""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from app.risk.canonical import decimal, require, seal
from app.risk.vocabulary import RiskState
from app.strategy_lab.numeric import difference, exact_product, ratio
from .policy import RiskStatePolicy, STATES


class StateTransition(str, Enum):
    UNCHANGED = "UNCHANGED"
    ESCALATED = "ESCALATED"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True, slots=True)
class DrawdownMeasurement:
    historical_peak: Decimal
    current_equity: Decimal
    derived_peak: Decimal = field(init=False)
    drawdown_amount: Decimal = field(init=False)
    drawdown_fraction: Decimal = field(init=False)
    measurement_identity: str = field(init=False)

    def __post_init__(self):
        decimal(self.historical_peak, "historical peak", positive=True)
        decimal(self.current_equity, "current equity")
        object.__setattr__(self, "derived_peak", max(self.historical_peak, self.current_equity))
        amount = max(difference(self.historical_peak, self.current_equity), Decimal("0"))
        object.__setattr__(self, "drawdown_amount", amount)
        object.__setattr__(self, "drawdown_fraction", ratio(amount, self.historical_peak))
        seal(self, "drawdown-measurement-1.0.0", "measurement_identity")


def transition_state(measurement, previous, policy):
    require(measurement, DrawdownMeasurement, "measurement")
    require(previous, RiskState, "previous state")
    require(policy, RiskStatePolicy, "state policy")
    entry = 0
    # Compare exact monetary amounts, not a rounded repeating decimal ratio.
    for index, state in enumerate(STATES[1:], 1):
        threshold = getattr(policy, state.value.lower() + "_entry")
        if measurement.drawdown_amount >= exact_product(measurement.historical_peak, threshold):
            entry = index
    prior = STATES.index(previous)
    if entry > prior:
        return STATES[entry], StateTransition.ESCALATED
    if prior > entry:
        recovery = getattr(policy, previous.value.lower() + "_recovery")
        if measurement.drawdown_amount <= exact_product(measurement.historical_peak, recovery):
            return STATES[prior - 1], StateTransition.RECOVERED
    return previous, StateTransition.UNCHANGED
