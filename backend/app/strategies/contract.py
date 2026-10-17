"""Single-observation contract; no family rules, backtest loop or execution."""
from typing import Protocol, runtime_checkable
from .configuration import StrategyDefinition
from .inputs import StrategyInput
from .decisions import StrategyDecision


@runtime_checkable
class Strategy(Protocol):
    """Implementations must be pure and deterministic for identical inputs.

    They may inspect only supplied canonical data at T and earlier. Providers,
    databases, execution venues, orders, sizing, clocks and randomness are outside
    this contract. Python structural typing is not an isolation/security sandbox.
    """
    @property
    def definition(self) -> StrategyDefinition: ...

    def decide(self, observation: StrategyInput) -> StrategyDecision: ...


def evaluate_strategy(strategy: Strategy, observation: StrategyInput) -> StrategyDecision:
    """Validate one declared input/output pair; never infer strategy rules."""
    if not isinstance(observation, StrategyInput) or not isinstance(strategy, Strategy):
        raise ValueError("A strategy contract and validated input are required")
    if not isinstance(strategy.definition, StrategyDefinition) or strategy.definition != observation.definition:
        raise ValueError("Strategy definition does not match the input configuration")
    decision = strategy.decide(observation)
    if not isinstance(decision, StrategyDecision):
        raise ValueError("Strategies must return a StrategyDecision")
    if (decision.strategy_family != observation.definition.family
        or decision.strategy_version != observation.definition.version
        or decision.configuration_identity != observation.definition.configuration_identity
        or decision.input_identity != observation.input_identity
        or decision.timestamp != observation.candle.close_time):
        raise ValueError("Decision does not describe the supplied analytical input")
    return decision
