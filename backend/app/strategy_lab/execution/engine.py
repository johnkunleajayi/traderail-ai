"""Historical next-open fills from already-produced causal decisions; research only."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from app.market_intelligence.types import TIMEFRAME_SECONDS
from app.strategies.decisions import DecisionType
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.dataset import ResearchDataset, create_dataset
from app.strategy_lab.numeric import require_decimal
from app.strategy_lab.replay.types import ReplayResult
from app.strategy_lab.replay.transitions import transition_state
from .config import HistoricalExecutionConfiguration, identity
from .pricing import price_fill
from .types import SimulatedFill, UnfilledDecision, ExecutionSimulation
from .validation import validate_replay


def simulate_execution(dataset: ResearchDataset, replay: ReplayResult, *, quantity: Decimal,
                       cost_model: ResearchCostModel, as_of: datetime) -> ExecutionSimulation:
    """Quantity is explicit base-asset units per action, never a capital/risk allocation.

    A fixed additional delay preserves action ordering. Filled state is changed only by
    available fills, independently of Part 1's immediate research thesis transitions.
    """
    require_decimal(quantity, "quantity")
    if quantity <= 0:
        raise ValueError("Research quantity must be positive")
    validate_replay(dataset, replay, as_of)
    config = HistoricalExecutionConfiguration(cost_model, dataset.context.timeframe)
    duration = timedelta(seconds=TIMEFRAME_SECONDS[dataset.context.timeframe])
    state, fills, unfilled, no_action = replay.initial_state, [], [], 0
    for index, record in enumerate(replay.records, start=replay.warmup_count):
        decision = record.decision
        if decision.decision is DecisionType.NO_ACTION:
            no_action += 1
            continue
        eligible_index = index + 1 + config.delay_candles
        try:
            eligible_time = (record.open_time + duration * (1 + config.delay_candles)).astimezone(timezone.utc)
        except OverflowError:
            raise ValueError("Execution delay exceeds representable historical timestamps") from None
        if eligible_index >= dataset.candle_count:
            unfilled.append(UnfilledDecision(decision, record.record_identity, record.dataset_prefix_fingerprint,
                decision.timestamp, eligible_time, record.position_before, state))
            continue
        candle = dataset.candles[eligible_index]
        if candle.open_time != eligible_time or candle.open_time <= decision.timestamp:
            raise ValueError("Eligible fill candle timestamp violates causal next-open timing")
        # Fixed delay makes available fills a prefix of actionable replay decisions.
        if record.position_before is not state:
            raise ValueError("Replay thesis does not match filled state at the eligible action")
        following = transition_state(state, decision.decision)
        pricing = price_fill(candle.open, decision.decision, quantity, cost_model)
        prefix = create_dataset(dataset.context, dataset.candles[:eligible_index+1],
            warmup_start=dataset.warmup_start, evaluation_start=dataset.evaluation_start,
            evaluation_end=candle.open_time+duration, as_of=as_of)
        fill_id = identity("strategy-lab.simulated-fill/v1", {
            "dataset_prefix": prefix.dataset_fingerprint, "decision_record_identity": record.record_identity,
            "decision_identity": decision.decision_identity, "configuration_identity": config.configuration_identity,
            "decision_timestamp": decision.timestamp, "eligible_fill_timestamp": eligible_time,
            "position_before": state, "position_after": following,
            "quantity": quantity, "pricing": asdict(pricing)})
        fills.append(SimulatedFill(prefix.dataset_fingerprint, record.record_identity, decision, dataset.context,
            decision.strategy_family, decision.strategy_version, decision.configuration_identity,
            decision.timestamp, eligible_time, state, following, decision.decision, quantity, pricing, config, fill_id))
        state = following
    simulation_id = identity("strategy-lab.execution-simulation/v1", {
        "dataset": dataset.dataset_fingerprint, "replay": replay.replay_identity,
        "configuration": config.configuration_identity, "quantity": quantity,
        "fills": [fill.fill_identity for fill in fills],
        "unfilled_decisions": [row.decision.decision_identity for row in unfilled]})
    return ExecutionSimulation(dataset.dataset_fingerprint, replay.replay_identity, config, quantity,
        replay.initial_state, state, tuple(fills), tuple(unfilled), no_action, simulation_id)
