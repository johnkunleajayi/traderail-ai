"""Immutable historical fill provenance, separate from replay thesis state."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from app.market_intelligence.types import DatasetContext
from app.strategies.decisions import StrategyDecision, DecisionType
from app.strategies.inputs import PositionState
from .config import HistoricalExecutionConfiguration
from .pricing import FillPricing


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    dataset_prefix_fingerprint: str
    decision_record_identity: str
    decision: StrategyDecision
    context: DatasetContext
    strategy_family: str
    strategy_version: str
    strategy_configuration_identity: str
    decision_timestamp: datetime
    eligible_fill_timestamp: datetime
    position_before: PositionState
    position_after: PositionState
    action: DecisionType
    quantity: Decimal
    pricing: FillPricing
    configuration: HistoricalExecutionConfiguration
    fill_identity: str


@dataclass(frozen=True, slots=True)
class UnfilledDecision:
    decision: StrategyDecision
    decision_record_identity: str
    decision_dataset_prefix_fingerprint: str
    decision_timestamp: datetime
    eligible_fill_timestamp: datetime
    replay_thesis_state_before: PositionState
    filled_state: PositionState
    reason_code: str = "END_OF_DATASET"


@dataclass(frozen=True, slots=True)
class ExecutionSimulation:
    dataset_fingerprint: str
    replay_identity: str
    configuration: HistoricalExecutionConfiguration
    quantity: Decimal
    initial_state: PositionState
    final_filled_state: PositionState
    fills: tuple[SimulatedFill, ...]
    unfilled_decisions: tuple[UnfilledDecision, ...]
    no_action_count: int
    simulation_identity: str
