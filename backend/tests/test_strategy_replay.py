"""Causal replay tests use synthetic frozen history, never providers or a database."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from itertools import product
from pathlib import Path
from uuid import UUID
import subprocess
import sys
import pytest
from app.market_intelligence.types import Candle, DatasetContext, TIMEFRAME_SECONDS
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_intelligence.engine import calculate_features
from app.market_regimes.engine import classify_regimes
from app.market_regimes.config import REGIME_CONFIG, candidate_first_index
from app.market_regimes.types import Regime
from app.strategies import contract
from app.strategies.configuration import StrategyDefinition
from app.strategies.inputs import PositionState, StrategyInput
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.adaptive_trend import AdaptiveTrendStrategy, AdaptiveTrendConfiguration
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.replay import replay_dataset, transition_state, ReplayRecord, ReplayResult
from app.strategy_lab.replay import engine

D = Decimal
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
AS_OF = START + timedelta(days=500)
CONTEXT = DatasetContext(UUID(int=1), "BTC/USDT", "BINANCE", "1h")


def dataset(count=56, warmup=52, *, context=CONTEXT, offset=0, scale=D(1)):
    duration = timedelta(seconds=TIMEFRAME_SECONDS[context.timeframe])
    rows = tuple(Candle(context.instrument_id, context.provider, context.timeframe,
        START + duration * (i + offset), START + duration * (i + offset + 1) - timedelta(milliseconds=1),
        D(100+i)*scale, D(102+i)*scale, D(99+i)*scale, D(101+i)*scale, D("1.25"),
        START + duration * (i + offset + 1) + timedelta(days=2)) for i in range(count))
    return create_dataset(context, rows, warmup_start=rows[0].open_time,
        evaluation_start=rows[warmup].open_time,
        evaluation_end=rows[-1].open_time + duration, as_of=AS_OF)


class RecordingStrategy:
    """Test contract implementation; the only retained objects are past inputs."""
    def __init__(self, actions=(), parameters=None):
        self.definition = StrategyDefinition("TEST_REPLAY", "1.0.0", parameters or {})
        self.actions = actions
        self.observations = []

    def decide(self, observation):
        self.observations.append(observation)
        index = len(self.observations) - 1
        action = self.actions[index] if index < len(self.actions) else DecisionType.NO_ACTION
        return StrategyDecision(observation, action, ("TEST_CAUSAL_OBSERVATION",),
            {"close": observation.intelligence.close, "position_state": observation.position_state.value})


def run(data=None, strategy=None, **kwargs):
    return replay_dataset(data or dataset(), strategy or AdaptiveTrendStrategy(), as_of=AS_OF, **kwargs)


def test_repeated_replay_is_identical_and_immutable():
    data = dataset()
    first, second = run(data), run(data)
    assert first == second
    assert first.replay_identity == second.replay_identity
    assert isinstance(first.records, tuple)
    with pytest.raises(FrozenInstanceError): first.final_state = PositionState.LONG
    with pytest.raises(FrozenInstanceError): first.records[0].position_before = PositionState.LONG
    with pytest.raises(FrozenInstanceError): first.records[0].intelligence.close = D(1)
    with pytest.raises(FrozenInstanceError): first.records[0].regime.evidence.confirmation_count = 1
    with pytest.raises(TypeError): first.records[0].decision.evidence["close"] = D(1)


def test_appending_future_candles_changes_full_run_but_not_earlier_records():
    earlier, later = run(dataset(55)), run(dataset(59))
    assert earlier.records == later.records[:len(earlier.records)]
    assert earlier.dataset_fingerprint != later.dataset_fingerprint
    assert earlier.replay_identity != later.replay_identity
    assert earlier.records[-1].dataset_prefix_fingerprint == earlier.dataset_fingerprint


def test_changed_future_prices_cannot_influence_earlier_records():
    original = dataset(58)
    rows = original.candles[:56] + tuple(replace(row, open=D(1), high=D(1000000),
        low=D("0.00001"), close=D(500000)) for row in original.candles[56:])
    altered = create_dataset(original.context, rows, warmup_start=original.warmup_start,
        evaluation_start=original.evaluation_start, evaluation_end=original.evaluation_end, as_of=AS_OF)
    assert run(original).records[:4] == run(altered).records[:4]


def test_only_prefix_reaches_analytics_and_strategy(monkeypatch):
    data, strategy = dataset(), RecordingStrategy()
    seen_features, seen_regimes, seen_contract = [], [], []
    real_features, real_regimes, real_evaluate = calculate_features, classify_regimes, contract.evaluate_strategy
    def features(context, rows, **kwargs):
        seen_features.append(tuple(rows))
        return real_features(context, rows, **kwargs)
    def regimes(rows, **kwargs):
        seen_regimes.append(tuple(rows))
        return real_regimes(rows, **kwargs)
    def evaluate(supplied, observation):
        seen_contract.append((supplied, observation))
        return real_evaluate(supplied, observation)
    monkeypatch.setattr(engine.intelligence_engine, "calculate_features", features)
    monkeypatch.setattr(engine.regime_engine, "classify_regimes", regimes)
    monkeypatch.setattr(contract, "evaluate_strategy", evaluate)
    result = run(data, strategy)
    assert [len(rows) for rows in seen_features] == [53, 54, 55, 56]
    assert [len(rows) for rows in seen_regimes] == [53, 54, 55, 56]
    for index, observation in enumerate(strategy.observations, start=52):
        assert seen_features[index-52] == data.candles[:index+1]
        assert max(row.open_time for row in seen_regimes[index-52]) == observation.candle.open_time
        assert seen_contract[index-52] == (strategy, observation)
        assert observation.regime.evidence.available_candles == index+1
        assert observation.intelligence.open_time == data.candles[index].open_time
        assert not hasattr(observation, "dataset") and not hasattr(observation, "history")
        assert not hasattr(observation, "evaluation_end")
        assert observation.candle == data.candles[index]
    assert len(result.records) == 4


def test_warmup_excluded_and_first_eligible_pending_confirmation_preserved():
    strategy = RecordingStrategy()
    result = run(dataset(53), strategy)
    assert result.warmup_count == 52 and result.candle_count == 53
    assert len(result.records) == len(strategy.observations) == 1
    row = result.records[0]
    assert row.open_time == result.evaluation_start
    assert row.regime.regime is Regime.UNKNOWN
    assert row.regime.evidence.confirmation_count == 1
    assert row.regime.evidence.confirmation_required == 3
    assert row.regime.evidence.candidate is Regime.BULL_TREND


@pytest.mark.parametrize("warmup", [0, 20, 49, 51])
def test_insufficient_analytical_warmup_rejected_before_strategy(warmup):
    strategy = RecordingStrategy()
    with pytest.raises(ValueError, match="warm-up"): run(dataset(warmup=warmup), strategy)
    assert strategy.observations == []


def test_warmup_requirement_derived_from_existing_config():
    config = replace(REGIME_CONFIG, slope_lookback=8)
    assert candidate_first_index(config) == 57
    with pytest.raises(ValueError, match="warm-up"):
        run(dataset(60, warmup=56), regime_configuration=config)
    assert run(dataset(60, warmup=57), regime_configuration=config).warmup_count == 57


def test_explicit_dataset_start_initializes_indicators_without_hidden_history():
    data = dataset(55, offset=15)
    result = run(data)
    for i, record in enumerate(result.records, start=52):
        expected = calculate_features(data.context, data.candles[:i+1], as_of=AS_OF)[-1]
        assert record.intelligence == expected
    assert result.warmup_start == START + timedelta(hours=15)
    assert result.records[-1].regime.evidence.available_candles == 55


VALID = {
    (PositionState.CASH, DecisionType.ENTER_LONG): PositionState.LONG,
    (PositionState.CASH, DecisionType.ENTER_SHORT): PositionState.SHORT,
    (PositionState.LONG, DecisionType.EXIT_LONG): PositionState.CASH,
    (PositionState.SHORT, DecisionType.EXIT_SHORT): PositionState.CASH,
    **{(state, DecisionType.NO_ACTION): state for state in PositionState},
}


@pytest.mark.parametrize("state,action", list(product(PositionState, DecisionType)))
def test_complete_transition_matrix(state, action):
    if (state, action) in VALID:
        assert transition_state(state, action) is VALID[state, action]
    else:
        with pytest.raises(ValueError, match="Invalid research thesis transition"):
            transition_state(state, action)


@pytest.mark.parametrize("state,action", [("CASH", DecisionType.NO_ACTION),
    (PositionState.CASH, "ENTER_LONG"), (None, None)])
def test_untyped_transitions_rejected(state, action):
    with pytest.raises(ValueError): transition_state(state, action)


def test_simulated_thesis_state_tracks_both_directions_without_reversal():
    actions = (DecisionType.ENTER_LONG, DecisionType.NO_ACTION, DecisionType.EXIT_LONG,
        DecisionType.ENTER_SHORT, DecisionType.NO_ACTION, DecisionType.EXIT_SHORT)
    result = run(dataset(58), RecordingStrategy(actions))
    assert [r.position_before for r in result.records] == [PositionState.CASH,
        PositionState.LONG, PositionState.LONG, PositionState.CASH, PositionState.SHORT, PositionState.SHORT]
    assert [r.position_after for r in result.records] == [PositionState.LONG,
        PositionState.LONG, PositionState.CASH, PositionState.SHORT, PositionState.SHORT, PositionState.CASH]
    assert result.final_state is PositionState.CASH
    assert [r.decision.decision for r in result.records] == list(actions)


@pytest.mark.parametrize("state", list(PositionState))
def test_explicit_initial_state_preserved_without_an_action(state):
    result = run(dataset(53), RecordingStrategy(), initial_state=state)
    assert result.initial_state is result.final_state is state
    assert result.records[0].position_before is state


def test_invalid_strategy_transition_rejects_run_without_reversal():
    with pytest.raises(ValueError, match="Invalid research thesis transition"):
        run(strategy=RecordingStrategy((DecisionType.ENTER_LONG, DecisionType.ENTER_SHORT)))


def test_original_retrieval_times_preserved_and_cutoffs_are_prefix_local():
    data, strategy = dataset(), RecordingStrategy()
    run(data, strategy)
    for index, observation in enumerate(strategy.observations, start=52):
        assert observation.candle.retrieved_at == data.candles[index].retrieved_at
        assert observation.as_of == max(row.retrieved_at for row in data.candles[:index+1])
        assert observation.as_of > observation.candle.close_time
    first = run(data)
    later_cutoff = replay_dataset(data, AdaptiveTrendStrategy(), as_of=AS_OF + timedelta(days=10))
    assert first == later_cutoff  # Informational verification clock is not research identity.


def test_exact_provenance_and_shared_adaptive_trend_contract():
    data, strategy = dataset(), AdaptiveTrendStrategy()
    result = run(data, strategy)
    assert result.dataset_fingerprint == data.dataset_fingerprint
    assert result.strategy_definition == strategy.definition
    assert result.feature_configuration == FEATURE_CONFIG
    assert result.regime_configuration == REGIME_CONFIG
    assert result.replay_version == "1.0.0"
    for index, row in enumerate(result.records, start=52):
        assert row.strategy_family == "ADAPTIVE_TREND" and row.strategy_version == "1.0.0"
        assert row.configuration_identity == strategy.definition.configuration_identity
        assert row.regime.configuration.engine_version == "1.0.0"
        assert row.regime.feature_engine_version == FEATURE_CONFIG.engine_version
        observation = StrategyInput(data.context, data.candles[index], row.intelligence,
            row.regime, strategy.definition, AS_OF, row.position_before)
        assert row.decision == contract.evaluate_strategy(strategy, observation)
        for value in (row.intelligence_identity, row.decision.input_identity,
            row.decision.decision_identity, row.record_identity):
            assert value.startswith("sha256:")


def test_meaningful_strategy_configuration_and_position_change_identity():
    data = dataset(53)
    original = run(data)
    config = AdaptiveTrendConfiguration()
    altered = run(data, AdaptiveTrendStrategy(replace(config, entry_min_ema_spread=config.entry_min_ema_spread+D(".001"))))
    assert original.replay_identity != altered.replay_identity
    assert original.records[0].decision.decision_identity != altered.records[0].decision.decision_identity
    long = run(data, initial_state=PositionState.LONG)
    assert original.records[0].decision.input_identity != long.records[0].decision.input_identity
    assert original.replay_identity != long.replay_identity


def test_dictionary_order_decimal_scale_and_ambient_precision_do_not_change_identity():
    first = run(strategy=RecordingStrategy(parameters={"a":D(".10"), "b":2}))
    with localcontext() as ctx:
        ctx.prec = 9
        second = run(strategy=RecordingStrategy(parameters={"b":2, "a":D(".1000")}))
    assert first == second


@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h", "1d"])
def test_supported_timeframe_context_preserved(timeframe):
    data = dataset(53, context=replace(CONTEXT, timeframe=timeframe))
    result = run(data)
    assert result.context == data.context
    assert result.records[0].regime.timeframe == timeframe


@pytest.mark.parametrize("scale", [D("1000"), D("0.000000000001")])
def test_decimal_source_values_preserved_without_float(scale):
    data = dataset(53, scale=scale)
    row = run(data).records[0]
    assert row.intelligence.close == data.candles[-1].close
    assert isinstance(row.intelligence.ema_20, D)


@pytest.mark.parametrize("damage", ["unordered", "duplicate", "gap", "provider", "timeframe",
    "instrument", "outside", "bad_price", "bad_ohlc", "fingerprint", "boundaries", "mutable_rows"])
def test_invalid_frozen_datasets_rejected_before_any_strategy_evaluation(damage):
    data, strategy = dataset(), RecordingStrategy()
    rows = list(data.candles)
    if damage == "unordered": rows[3], rows[4] = rows[4], rows[3]
    elif damage == "duplicate": rows[4] = rows[3]
    elif damage == "gap": rows.pop(4)
    elif damage in ("provider", "timeframe", "instrument"):
        name, value = {"provider":("provider","OTHER"), "timeframe":("timeframe","4h"),
            "instrument":("instrument_id", UUID(int=2))}[damage]
        rows[4] = replace(rows[4], **{name:value})
    elif damage == "outside": rows.append(replace(rows[-1], open_time=rows[-1].open_time+timedelta(hours=1),
        close_time=rows[-1].close_time+timedelta(hours=1), retrieved_at=AS_OF))
    elif damage == "bad_price": rows[4] = replace(rows[4], close=D("NaN"))
    elif damage == "bad_ohlc": rows[4] = replace(rows[4], high=D(1))
    data = replace(data, candles=rows if damage == "mutable_rows" else tuple(rows))
    if damage == "fingerprint": data = replace(data, dataset_fingerprint="sha256:forged")
    if damage == "boundaries": data = replace(data, evaluation_end=data.evaluation_end+timedelta(hours=1))
    with pytest.raises(ValueError): run(data, strategy)
    assert strategy.observations == []


def test_incomplete_candle_and_future_retrieval_rejected_before_evaluation():
    data, strategy = dataset(), RecordingStrategy()
    with pytest.raises(ValueError):
        replay_dataset(data, strategy, as_of=data.candles[-1].close_time)
    with pytest.raises(ValueError, match="retrieval"):
        replay_dataset(data, strategy, as_of=data.evaluation_end)
    assert strategy.observations == []


def test_tampering_with_valid_snapshot_content_rejected_by_fingerprint():
    data = dataset()
    row = replace(data.candles[0], volume=D(999))
    forged = replace(data, candles=(row, *data.candles[1:]))
    with pytest.raises(ValueError, match="fingerprint"): run(forged)


def test_bad_strategy_output_rejected_through_shared_contract():
    class Broken(RecordingStrategy):
        def decide(self, observation): return None
    with pytest.raises(ValueError, match="StrategyDecision"): run(strategy=Broken())


def test_no_execution_or_performance_fields_in_replay_records():
    forbidden = {"order", "fill", "entry_price", "exit_price", "quantity", "pnl", "equity", "fees", "balance", "trade_intent"}
    assert not forbidden.intersection(item.name for cls in (ReplayRecord, ReplayResult) for item in fields(cls))


def test_fresh_process_has_no_database_provider_or_fastapi_dependency():
    source = """
import sys
from app.strategy_lab.replay import replay_dataset
for name in sys.modules:
    assert not any(name == x or name.startswith(x + '.') for x in
        ('sqlalchemy', 'fastapi', 'httpx', 'requests', 'app.db', 'app.market_data', 'app.core.config'))
print('pure replay imports')
"""
    result = subprocess.run([sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "pure replay imports" in result.stdout


def test_source_snapshot_is_not_mutated_by_replay():
    data = dataset()
    before = (data.dataset_fingerprint, data.context, data.candles,
        data.warmup_start, data.evaluation_start, data.evaluation_end)
    run(data)
    assert before == (data.dataset_fingerprint, data.context, data.candles,
        data.warmup_start, data.evaluation_start, data.evaluation_end)


def test_definition_drift_cannot_bypass_shared_contract():
    class Drifting(RecordingStrategy):
        def decide(self, observation):
            result = super().decide(observation)
            self.definition = StrategyDefinition("TEST_REPLAY", "2.0.0", {})
            return result
    with pytest.raises(ValueError, match="does not match"):
        run(strategy=Drifting())


@pytest.mark.parametrize("bad", ["dataset", "state", "strategy", "regime_config", "as_of"])
def test_invalid_replay_arguments_rejected(bad):
    arguments = {"dataset":dataset(), "strategy":AdaptiveTrendStrategy(), "as_of":AS_OF}
    key, value = {"dataset":("dataset",None), "state":("initial_state","CASH"),
        "strategy":("strategy",object()), "regime_config":("regime_configuration",None),
        "as_of":("as_of",START.replace(tzinfo=None))}[bad]
    arguments[key] = value
    with pytest.raises(ValueError): replay_dataset(**arguments)
