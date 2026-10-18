"""Immutable replay provenance; full snapshot identity is separate from causal records."""
from dataclasses import dataclass
from datetime import datetime
from app.market_intelligence.config import FeatureConfiguration
from app.market_intelligence.types import DatasetContext, FeatureRow
from app.market_regimes.config import RegimeConfiguration
from app.market_regimes.types import RegimeResult
from app.strategies.configuration import StrategyDefinition
from app.strategies.decisions import StrategyDecision
from app.strategies.inputs import PositionState

REPLAY_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    # Exact frozen prefix fingerprint, not the future-sensitive full run fingerprint.
    dataset_prefix_fingerprint: str
    context: DatasetContext
    strategy_family: str
    strategy_version: str
    configuration_identity: str
    open_time: datetime
    close_time: datetime
    position_before: PositionState
    position_after: PositionState
    intelligence: FeatureRow
    intelligence_identity: str
    regime: RegimeResult
    decision: StrategyDecision
    record_identity: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    replay_version: str
    dataset_fingerprint: str
    context: DatasetContext
    warmup_start: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    candle_count: int
    warmup_count: int
    strategy_definition: StrategyDefinition
    feature_configuration: FeatureConfiguration
    regime_configuration: RegimeConfiguration
    initial_state: PositionState
    final_state: PositionState
    records: tuple[ReplayRecord, ...]
    replay_identity: str
