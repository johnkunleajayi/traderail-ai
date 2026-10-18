"""Check snapshot/replay provenance before simulating any fill; no recalculation."""
from dataclasses import asdict
from datetime import datetime, timezone
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_intelligence.types import FeatureRow
from app.market_regimes.types import Evidence, RegimeResult
from app.market_regimes.config import RegimeConfiguration, candidate_first_index
from app.strategies.configuration import StrategyDefinition
from app.strategies.inputs import StrategyInput, PositionState
from app.strategies.decisions import StrategyDecision
from app.strategy_lab.canonical import utc_timestamp
from app.strategy_lab.dataset import ResearchDataset, create_dataset
from app.strategy_lab.replay.types import ReplayRecord, ReplayResult, REPLAY_VERSION
from app.strategy_lab.replay.transitions import transition_state
from .config import identity


def validate_replay(dataset: ResearchDataset, replay: ReplayResult, as_of: datetime) -> None:
    utc_timestamp(as_of)
    if not isinstance(dataset, ResearchDataset) or not isinstance(dataset.candles, tuple):
        raise ValueError("An immutable research dataset is required")
    checked = create_dataset(dataset.context, dataset.candles, warmup_start=dataset.warmup_start,
        evaluation_start=dataset.evaluation_start, evaluation_end=dataset.evaluation_end, as_of=as_of)
    if checked.dataset_fingerprint != dataset.dataset_fingerprint:
        raise ValueError("Dataset contents do not match the frozen fingerprint")
    if any(row.retrieved_at > as_of for row in dataset.candles):
        raise ValueError("Future retrieval relative to the archival cutoff")
    if (not isinstance(replay, ReplayResult) or replay.replay_version != REPLAY_VERSION
        or not isinstance(replay.strategy_definition, StrategyDefinition)
        or not isinstance(replay.regime_configuration, RegimeConfiguration)
        or replay.feature_configuration != FEATURE_CONFIG
        or not isinstance(replay.initial_state, PositionState)
        or not isinstance(replay.candle_count, int) or isinstance(replay.candle_count, bool)
        or not isinstance(replay.warmup_count, int) or isinstance(replay.warmup_count, bool)
        or not isinstance(replay.records, tuple)):
        raise ValueError("A supported immutable causal replay is required")
    if (replay.context != dataset.context or replay.dataset_fingerprint != dataset.dataset_fingerprint
        or (replay.warmup_start, replay.evaluation_start, replay.evaluation_end) !=
           (dataset.warmup_start, dataset.evaluation_start, dataset.evaluation_end)
        or replay.candle_count != dataset.candle_count or replay.warmup_count != len(dataset.warmup_candles)
        or len(replay.records) != len(dataset.evaluation_candles)
        or replay.warmup_count < candidate_first_index(replay.regime_configuration)):
        raise ValueError("Replay and frozen dataset context/boundaries/counts do not match")
    state = replay.initial_state
    definition = replay.strategy_definition
    for index, record in enumerate(replay.records, start=replay.warmup_count):
        candle = dataset.candles[index]
        if (not isinstance(record, ReplayRecord) or not isinstance(record.decision, StrategyDecision)
            or not isinstance(record.intelligence, FeatureRow) or not isinstance(record.regime, RegimeResult)
            or not isinstance(record.regime.evidence, Evidence)
            or record.context != dataset.context or (record.open_time, record.close_time) != (candle.open_time, candle.close_time)
            or record.position_before is not state
            or (record.strategy_family, record.strategy_version, record.configuration_identity) !=
               (definition.family, definition.version, definition.configuration_identity)):
            raise ValueError("Replay record context, timestamp or thesis state mismatch")
        if (record.regime.configuration != replay.regime_configuration
            or record.regime.feature_engine_version != replay.feature_configuration.engine_version
            or record.regime.evidence.available_candles != index + 1):
            raise ValueError("Replay analytical context/version does not match its causal prefix")
        # Reconstruct shared input/decision validation, not indicator/regime/strategy rules.
        observation = StrategyInput(dataset.context, candle, record.intelligence, record.regime,
            definition, as_of.astimezone(timezone.utc), state)
        decision = StrategyDecision(observation, record.decision.decision,
            record.decision.reason_codes, record.decision.evidence)
        if decision != record.decision:
            raise ValueError("Replay StrategyDecision identity or timestamp mismatch")
        state = transition_state(state, decision.decision)
        if record.position_after is not state:
            raise ValueError("Invalid recorded replay transition")
        prefix = create_dataset(dataset.context, dataset.candles[:index+1], warmup_start=dataset.warmup_start,
            evaluation_start=dataset.evaluation_start, evaluation_end=dataset.candles[index+1].open_time
                if index+1 < dataset.candle_count else dataset.evaluation_end, as_of=as_of)
        feature_id = identity("strategy-lab.replay-intelligence/v1", {
            "dataset_prefix": prefix.dataset_fingerprint,
            "configuration": asdict(replay.feature_configuration), "row": asdict(record.intelligence)})
        record_id = identity("strategy-lab.replay-record/v1", {
            "replay_version": REPLAY_VERSION, "dataset_prefix": prefix.dataset_fingerprint,
            "intelligence_identity": feature_id, "decision_identity": decision.decision_identity,
            "position_after": state})
        if (record.dataset_prefix_fingerprint, record.intelligence_identity, record.record_identity) != (
                prefix.dataset_fingerprint, feature_id, record_id):
            raise ValueError("Replay prefix/analytical/record identity mismatch")
    expected = identity("strategy-lab.replay/v1", {"replay_version": REPLAY_VERSION,
        "dataset": dataset.dataset_fingerprint, "strategy_configuration": definition.configuration_identity,
        "feature_configuration": asdict(replay.feature_configuration), "regime_configuration": asdict(replay.regime_configuration),
        "initial_state": replay.initial_state, "records": [row.record_identity for row in replay.records]})
    if state is not replay.final_state or expected != replay.replay_identity:
        raise ValueError("Replay final thesis state or run identity mismatch")
