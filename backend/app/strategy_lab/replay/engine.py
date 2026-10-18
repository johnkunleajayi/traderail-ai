"""Transparent prefix replay. No providers, database, execution or metrics."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from app.market_intelligence import engine as intelligence_engine
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_intelligence.types import TIMEFRAME_SECONDS
from app.market_regimes import engine as regime_engine
from app.market_regimes.config import REGIME_CONFIG, RegimeConfiguration, candidate_first_index
from app.strategies import contract
from app.strategies.configuration import StrategyDefinition
from app.strategies.inputs import PositionState, StrategyInput, canonical_values
from app.strategy_lab.canonical import canonical_json, fingerprint, utc_timestamp
from app.strategy_lab.dataset import ResearchDataset, create_dataset
from .transitions import transition_state
from .types import REPLAY_VERSION, ReplayRecord, ReplayResult


def _identity(namespace, payload):
    return fingerprint(namespace, canonical_json(canonical_values(payload)))


def replay_dataset(dataset: ResearchDataset, strategy: contract.Strategy, *, as_of: datetime,
                   initial_state: PositionState = PositionState.CASH,
                   regime_configuration: RegimeConfiguration = REGIME_CONFIG) -> ReplayResult:
    """Replay explicit completed history only, starting analytical initialization at row 0.

    `as_of` is the caller's archival validation cutoff, not a simulated execution clock.
    Each observation's cutoff uses only that prefix's completion/retrieval times. Original
    retrieval provenance is preserved even when history was collected after candle close.
    """
    utc_timestamp(as_of)
    cutoff = as_of.astimezone(timezone.utc)
    if not isinstance(dataset, ResearchDataset) or not isinstance(dataset.candles, tuple):
        raise ValueError("An immutable frozen research dataset is required")
    if not isinstance(initial_state, PositionState):
        raise ValueError("An explicit initial thesis state is required")
    if not isinstance(regime_configuration, RegimeConfiguration):
        raise ValueError("A versioned regime configuration is required")
    if not isinstance(strategy, contract.Strategy) or not isinstance(strategy.definition, StrategyDefinition):
        raise ValueError("A declared strategy contract is required")
    definition = strategy.definition
    # Revalidate before any strategy evaluation, including snapshots built without the factory.
    checked = create_dataset(dataset.context, dataset.candles, warmup_start=dataset.warmup_start,
        evaluation_start=dataset.evaluation_start, evaluation_end=dataset.evaluation_end, as_of=cutoff)
    if checked.dataset_fingerprint != dataset.dataset_fingerprint:
        raise ValueError("Frozen dataset fingerprint does not match its contents/boundaries")
    if any(row.retrieved_at.astimezone(timezone.utc) > cutoff for row in dataset.candles):
        raise ValueError("Dataset retrieval is later than the archival validation cutoff")
    warmup_count = len(dataset.warmup_candles)
    if warmup_count < candidate_first_index(regime_configuration):
        raise ValueError("Evaluation begins before the required analytical warm-up")
    duration = timedelta(seconds=TIMEFRAME_SECONDS[dataset.context.timeframe])
    records, state = [], initial_state
    for index in range(warmup_count, len(dataset.candles)):
        prefix = dataset.candles[:index + 1]
        candle = prefix[-1]
        observed_at = max(candle.open_time + duration, *(row.retrieved_at for row in prefix))
        features = intelligence_engine.calculate_features(dataset.context, prefix, as_of=observed_at)
        regimes = regime_engine.classify_regimes(features, as_of=observed_at, config=regime_configuration)
        observation = StrategyInput(dataset.context, candle, features[-1], regimes[-1],
            definition, observed_at, state)
        decision = contract.evaluate_strategy(strategy, observation)
        following = transition_state(state, decision.decision)
        prefix_snapshot = create_dataset(dataset.context, prefix, warmup_start=dataset.warmup_start,
            evaluation_start=dataset.evaluation_start, evaluation_end=candle.open_time + duration,
            as_of=observed_at)
        feature_identity = _identity("strategy-lab.replay-intelligence/v1", {
            "dataset_prefix": prefix_snapshot.dataset_fingerprint,
            "configuration": asdict(FEATURE_CONFIG), "row": asdict(features[-1])})
        record_identity = _identity("strategy-lab.replay-record/v1", {
            "replay_version": REPLAY_VERSION, "dataset_prefix": prefix_snapshot.dataset_fingerprint,
            "intelligence_identity": feature_identity, "decision_identity": decision.decision_identity,
            "position_after": following})
        records.append(ReplayRecord(prefix_snapshot.dataset_fingerprint, dataset.context,
            definition.family, definition.version, definition.configuration_identity,
            features[-1].open_time, features[-1].close_time, state, following,
            features[-1], feature_identity, regimes[-1], decision, record_identity))
        state = following
    replay_identity = _identity("strategy-lab.replay/v1", {
        "replay_version": REPLAY_VERSION, "dataset": dataset.dataset_fingerprint,
        "strategy_configuration": definition.configuration_identity,
        "feature_configuration": asdict(FEATURE_CONFIG), "regime_configuration": asdict(regime_configuration),
        "initial_state": initial_state, "records": [row.record_identity for row in records]})
    return ReplayResult(REPLAY_VERSION, dataset.dataset_fingerprint, dataset.context,
        dataset.warmup_start, dataset.evaluation_start, dataset.evaluation_end,
        dataset.candle_count, warmup_count, definition, FEATURE_CONFIG, regime_configuration,
        initial_state, state, tuple(records), replay_identity)
