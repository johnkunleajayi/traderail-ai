"""Paired validation uses real analytics/replay/accounting and frozen synthetic candles."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path
from uuid import UUID
import subprocess
import sys
import pytest

from app.market_intelligence.types import Candle, DatasetContext
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.configuration import StrategyDefinition
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.inputs import PositionState
from app.strategy_lab.accounting.config import ACCOUNTING_CONFIG
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration
from app.strategy_lab.out_of_sample import OutOfSamplePlan, validate_out_of_sample, compare_performance
from app.strategy_lab.validation import TimeWindow, ValidationSplit

D = Decimal
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
AS_OF = START + timedelta(days=30)
CONTEXT = DatasetContext(UUID(int=1), "BTC/USDT", "BINANCE", "1h")
ZERO_COST = ResearchCostModel(D(0), D(0), D(0))


def at(index):
    return START + timedelta(hours=index)


def dataset(count=68, warmup=52, changes=None):
    changes = changes or {}
    rows = []
    for i in range(count):
        close = changes.get(i, D(100))
        rows.append(Candle(CONTEXT.instrument_id, CONTEXT.provider, CONTEXT.timeframe,
            at(i), at(i+1)-timedelta(milliseconds=1), close, close+D(1), close-D(1), close,
            D(10), at(i+1)+timedelta(days=1)))
    return create_dataset(CONTEXT, tuple(rows), warmup_start=START, evaluation_start=at(warmup),
        evaluation_end=at(count), as_of=AS_OF)


class ScheduledContractFixture:
    # Test-only predetermined causal schedule, never a product strategy or parameter optimizer.
    def __init__(self, actions=(), parameters=None):
        self.actions = dict(actions)
        self.definition = StrategyDefinition("TEST_OOS", "1.0.0", parameters or {"fixture": "causal"})

    def decide(self, observation):
        action = self.actions.get(observation.candle.open_time, DecisionType.NO_ACTION)
        return StrategyDecision(observation, action, ("TEST_CAUSAL_FIXTURE",),
            {"close": observation.candle.close, "position_state": observation.position_state.value})


def plan(data=None, *, strategy=None, split_at=60, costs=ZERO_COST, quantity=D(1), capital=D(10000)):
    data = data or dataset()
    strategy = strategy or AdaptiveTrendStrategy()
    return OutOfSamplePlan(data, TimeWindow(data.warmup_start, data.evaluation_start),
        ValidationSplit(TimeWindow(data.evaluation_start, at(split_at)), TimeWindow(at(split_at), data.evaluation_end)),
        strategy.definition, HistoricalExecutionConfiguration(costs, data.context.timeframe), quantity, capital)


@lru_cache(None)
def flat_result():
    return validate_out_of_sample(plan(), AdaptiveTrendStrategy, as_of=AS_OF)


def test_actual_unchanged_adaptive_trend_no_trade_periods_are_independent():
    result = flat_result()
    for period, start, end in ((result.in_sample, at(52), at(60)), (result.out_of_sample, at(60), at(68))):
        assert period.replay.strategy_definition == AdaptiveTrendStrategy().definition
        assert period.replay.evaluation_start == start and period.replay.evaluation_end == end
        assert period.replay.initial_state is PositionState.CASH
        assert len(period.replay.records) == len(period.equity_curve) == 8
        assert period.performance.trade_count == 0
        assert period.performance.win_rate is period.performance.profit_factor is None
        assert period.performance.total_return == period.performance.exposure_percentage == 0
        assert all(row.total_equity == D(10000) for row in period.equity_curve)
        assert all(start <= row.open_time < end for row in period.replay.records)
    assert result.in_sample is not result.out_of_sample
    assert result.in_sample.dataset_fingerprint != result.out_of_sample.dataset_fingerprint
    assert result.out_of_sample.replay.warmup_count == 60
    assert result.plan.oos_analytical_context == TimeWindow(START, at(60))


def test_frozen_plan_and_result_with_full_configuration_provenance():
    result = flat_result()
    with pytest.raises(FrozenInstanceError):
        result.plan.quantity = D(2)
    with pytest.raises(FrozenInstanceError):
        result.validation_identity = "changed"
    with pytest.raises(TypeError):
        result.plan.strategy_definition.parameters["entry_fast_slope"] = D(1)
    assert result.plan.dataset_identity == result.plan.dataset.dataset_fingerprint
    assert result.plan.configuration()["validation_engine_version"] == "1.0.0"
    assert result.plan.configuration()["execution"] == result.in_sample.execution.configuration.configuration()
    assert result.in_sample.accounting_configuration == result.out_of_sample.accounting_configuration == ACCOUNTING_CONFIG
    assert isinstance(result.comparison, tuple)


def test_no_oos_leakage_into_any_is_artifact():
    original = flat_result()
    changed = validate_out_of_sample(plan(dataset(changes={i: D(100+i) for i in range(60, 68)})),
        AdaptiveTrendStrategy, as_of=AS_OF)
    assert changed.in_sample == original.in_sample
    assert changed.plan.strategy_definition == original.plan.strategy_definition
    assert changed.plan.strategy_definition.configuration_identity == original.plan.strategy_definition.configuration_identity
    assert changed.plan.dataset_identity != original.plan.dataset_identity
    assert changed.validation_identity != original.validation_identity


def test_later_oos_data_cannot_change_earlier_oos_records_or_equity():
    original = flat_result()
    changed = validate_out_of_sample(plan(dataset(changes={67: D(110)})), AdaptiveTrendStrategy, as_of=AS_OF)
    assert changed.in_sample == original.in_sample
    assert changed.out_of_sample.replay.records[:-1] == original.out_of_sample.replay.records[:-1]
    assert changed.out_of_sample.equity_curve[:-1] == original.out_of_sample.equity_curve[:-1]


def test_appended_oos_history_preserves_all_prior_causal_records():
    original = flat_result()
    extended = validate_out_of_sample(plan(dataset(70)), AdaptiveTrendStrategy, as_of=AS_OF)
    assert extended.in_sample == original.in_sample
    assert extended.out_of_sample.replay.records[:8] == original.out_of_sample.replay.records
    assert extended.out_of_sample.equity_curve[:8] == original.out_of_sample.equity_curve
    assert extended.validation_identity != original.validation_identity


def test_repeat_execution_and_inspection_cutoff_do_not_change_identity():
    result = validate_out_of_sample(plan(), AdaptiveTrendStrategy, as_of=AS_OF)
    assert result == flat_result()
    assert validate_out_of_sample(plan(), AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=1)) == result


@pytest.mark.parametrize("direction,entry", [(PositionState.LONG, DecisionType.ENTER_LONG),
                                              (PositionState.SHORT, DecisionType.ENTER_SHORT)])
def test_open_is_boundary_and_open_oos_end_do_not_carry_positions(direction, entry):
    actions = ((at(52), entry), (at(60), entry))
    factory = lambda: ScheduledContractFixture(actions)
    result = validate_out_of_sample(plan(strategy=factory()), factory, as_of=AS_OF)
    assert result.in_sample.open_position.direction is direction
    assert result.out_of_sample.open_position.direction is direction
    assert result.out_of_sample.replay.records[0].position_before is PositionState.CASH
    assert result.in_sample.open_position.entry_fill.eligible_fill_timestamp == at(53)
    assert result.out_of_sample.open_position.entry_fill.eligible_fill_timestamp == at(61)
    assert result.out_of_sample.initial_capital == D(10000)
    assert result.out_of_sample.execution.initial_state is PositionState.CASH
    assert not result.in_sample.completed_trades and not result.out_of_sample.completed_trades


def test_final_is_decision_stays_unfilled_and_cannot_fill_in_oos():
    factory = lambda: ScheduledContractFixture(((at(59), DecisionType.ENTER_LONG),))
    result = validate_out_of_sample(plan(strategy=factory()), factory, as_of=AS_OF)
    assert len(result.in_sample.execution.unfilled_decisions) == 1
    assert not result.in_sample.execution.fills and result.in_sample.open_position is None
    assert not result.out_of_sample.execution.fills and result.out_of_sample.open_position is None
    assert result.out_of_sample.replay.initial_state is PositionState.CASH


def test_literal_separate_period_metrics_and_cost_comparison():
    actions = ((at(52), DecisionType.ENTER_LONG), (at(54), DecisionType.EXIT_LONG),
               (at(60), DecisionType.ENTER_LONG), (at(62), DecisionType.EXIT_LONG))
    factory = lambda: ScheduledContractFixture(actions)
    data = dataset(changes={55: D(110), 63: D(90)})
    result = validate_out_of_sample(plan(data, strategy=factory()), factory, as_of=AS_OF)
    assert result.in_sample.completed_trades[0].net_pnl == D(10)
    assert result.out_of_sample.completed_trades[0].net_pnl == D(-10)
    assert result.in_sample.performance.net_return == D(".001")
    assert result.out_of_sample.performance.net_return == D("-.001")
    rows = {row.metric: row for row in result.comparison}
    assert rows["net_return"].oos_minus_is == D("-.002")
    assert rows["trade_count"].in_sample == rows["trade_count"].out_of_sample == 1
    assert rows["win_rate"].in_sample == 1 and rows["win_rate"].out_of_sample == 0
    assert rows["profit_factor"].oos_minus_is is None
    assert rows["total_fees"].oos_minus_is == 0
    assert rows["total_spread_cost"].oos_minus_is == rows["total_slippage_cost"].oos_minus_is == 0
    assert set(rows) == {"total_return", "net_return", "maximum_drawdown", "trade_count", "win_rate",
        "profit_factor", "expectancy", "exposure_percentage", "turnover", "total_fees", "total_spread_cost", "total_slippage_cost"}


def test_comparison_decimal_subtraction_preserves_precision_and_undefined_values():
    source = flat_result().in_sample.performance
    ins = replace(source, expectancy=D("12345678901234567890.123456789"))
    oos = replace(source, expectancy=D("12345678901234567890.123456790"))
    with localcontext() as context:
        context.prec = 3
        rows = {row.metric: row for row in compare_performance(ins, oos)}
    assert rows["expectancy"].oos_minus_is == D(".000000001")
    assert rows["profit_factor"].oos_minus_is is None


@pytest.mark.parametrize("change", ["quantity", "capital", "costs", "delay", "boundary", "strategy"])
def test_meaningful_changes_alter_plan_and_validation_identities(change):
    baseline = flat_result()
    kwargs = {}
    factory = AdaptiveTrendStrategy
    if change == "quantity": kwargs["quantity"] = D(2)
    if change == "capital": kwargs["capital"] = D(20000)
    if change == "costs": kwargs["costs"] = ResearchCostModel(D(".001"), D(0), D(0))
    if change == "delay": kwargs["costs"] = ResearchCostModel(D(0), D(0), D(0), timedelta(hours=1))
    if change == "boundary": kwargs["split_at"] = 61
    if change == "strategy":
        from app.strategies.adaptive_trend import AdaptiveTrendConfiguration
        configuration = replace(AdaptiveTrendConfiguration(), entry_min_ema_spread=D(".002"))
        factory = lambda: AdaptiveTrendStrategy(configuration)
        kwargs["strategy"] = factory()
    result = validate_out_of_sample(plan(**kwargs), factory, as_of=AS_OF)
    assert result.plan.plan_identity != baseline.plan.plan_identity
    assert result.validation_identity != baseline.validation_identity


def test_dictionary_order_and_equivalent_decimals_do_not_change_identity():
    left = ScheduledContractFixture(parameters={"alpha": D("1.00"), "beta": {"x": 2}})
    right = ScheduledContractFixture(parameters={"beta": {"x": 2}, "alpha": D("1")})
    first = validate_out_of_sample(plan(strategy=left, quantity=D("1.00")),
        lambda: ScheduledContractFixture(parameters=left.definition.parameters), as_of=AS_OF)
    second = validate_out_of_sample(plan(strategy=right, quantity=D(1)),
        lambda: ScheduledContractFixture(parameters=right.definition.parameters), as_of=AS_OF)
    assert first.plan.plan_identity == second.plan.plan_identity
    assert first.validation_identity == second.validation_identity
    assert first.comparison == second.comparison


@pytest.mark.parametrize("quantity,capital", [(D(0), D(100)), (D(-1), D(100)),
    (D(1), D(0)), (D(1), D(-1)), (1.0, D(100)), (D("NaN"), D(100))])
def test_invalid_financial_inputs_rejected(quantity, capital):
    with pytest.raises(ValueError): plan(quantity=quantity, capital=capital)


def test_unaffordable_quantity_is_rejected_not_resized():
    factory = lambda: ScheduledContractFixture(((at(52), DecisionType.ENTER_LONG),))
    with pytest.raises(ValueError):
        validate_out_of_sample(plan(strategy=factory(), capital=D(50)), factory, as_of=AS_OF)


@pytest.mark.parametrize("changes", [
    {"split": ValidationSplit(TimeWindow(at(52), at(59)), TimeWindow(at(60), at(68)))},
    {"split": ValidationSplit(TimeWindow(at(53), at(60)), TimeWindow(at(60), at(68)))},
    {"split": ValidationSplit(TimeWindow(at(52), at(60)), TimeWindow(at(60), at(69)))},
    {"split": ValidationSplit(TimeWindow(at(52), at(60)), TimeWindow(at(60), at(67)))},
    {"warmup": TimeWindow(at(1), at(52))},
    {"initial_state": PositionState.LONG},
    {"accounting_configuration": replace(ACCOUNTING_CONFIG, version="different")},
    {"execution_configuration": HistoricalExecutionConfiguration(ZERO_COST, "4h")},
])
def test_invalid_boundaries_or_unsupported_conventions_rejected(changes):
    with pytest.raises(ValueError): replace(plan(), **changes)


def test_reversed_and_overlapping_windows_rejected_by_shared_9a_definitions():
    with pytest.raises(ValueError): TimeWindow(at(60), at(52))
    with pytest.raises(ValueError): ValidationSplit(TimeWindow(at(52), at(61)), TimeWindow(at(60), at(68)))
    with pytest.raises(ValueError): ValidationSplit(TimeWindow(at(60), at(68)), TimeWindow(at(52), at(60)))


def test_insufficient_warmup_rejected_without_extra_history():
    with pytest.raises(ValueError, match="warm-up"):
        plan(dataset(warmup=10))


def test_forged_frozen_dataset_fingerprint_rejected():
    with pytest.raises(ValueError, match="fingerprint"):
        plan(replace(dataset(), dataset_fingerprint="forged"))


def test_factory_must_match_frozen_configuration_and_produce_fresh_instances():
    p = plan()
    with pytest.raises(ValueError, match="configuration"):
        validate_out_of_sample(p, ScheduledContractFixture, as_of=AS_OF)
    same = AdaptiveTrendStrategy()
    with pytest.raises(ValueError, match="fresh"):
        validate_out_of_sample(p, lambda: same, as_of=AS_OF)


def test_archival_cutoff_rejects_incomplete_dataset_before_factory_runs():
    called = []
    def factory():
        called.append(True)
        return AdaptiveTrendStrategy()
    with pytest.raises(ValueError): validate_out_of_sample(plan(), factory, as_of=at(60))
    assert not called


def test_actual_adaptive_trend_open_is_position_is_not_carried():
    from test_integrated_9c_audit import frozen_dataset
    data = frozen_dataset("long_loss")
    p = OutOfSamplePlan(data, TimeWindow(data.warmup_start, data.evaluation_start),
        ValidationSplit(TimeWindow(data.evaluation_start, at(132)), TimeWindow(at(132), data.evaluation_end)),
        AdaptiveTrendStrategy().definition, HistoricalExecutionConfiguration(ZERO_COST, "1h"), D(1), D(10000))
    result = validate_out_of_sample(p, AdaptiveTrendStrategy, as_of=AS_OF)
    assert result.in_sample.open_position.direction is PositionState.LONG
    assert result.out_of_sample.replay.records[0].position_before is PositionState.CASH
    assert result.in_sample.replay.records[-1].position_after is PositionState.LONG


def test_fresh_process_has_no_database_provider_or_api_dependency():
    script = """import sys
from app.strategy_lab.out_of_sample import OutOfSamplePlan, validate_out_of_sample
for prefix in ('sqlalchemy', 'psycopg', 'httpx', 'fastapi', 'app.db', 'app.market_data', 'app.auth'):
    assert not any(name == prefix or name.startswith(prefix+'.') for name in sys.modules), prefix
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("active_period", ["IN_SAMPLE", "OUT_OF_SAMPLE"])
def test_no_trades_in_one_period_does_not_pool_other_period_results(active_period):
    start = 52 if active_period == "IN_SAMPLE" else 60
    actions = ((at(start), DecisionType.ENTER_LONG), (at(start+2), DecisionType.EXIT_LONG))
    factory = lambda: ScheduledContractFixture(actions)
    result = validate_out_of_sample(plan(strategy=factory()), factory, as_of=AS_OF)
    active, empty = ((result.in_sample, result.out_of_sample) if active_period == "IN_SAMPLE"
                     else (result.out_of_sample, result.in_sample))
    assert active.performance.trade_count == 1
    assert empty.performance.trade_count == 0
    assert empty.performance.win_rate is empty.performance.profit_factor is None
    assert not empty.execution.fills and not empty.completed_trades
    assert empty.performance.exposure_percentage == empty.performance.turnover == 0


def test_nonzero_cost_totals_have_independent_literal_descriptive_deltas():
    actions = ((at(52), DecisionType.ENTER_LONG), (at(54), DecisionType.EXIT_LONG),
               (at(60), DecisionType.ENTER_LONG), (at(62), DecisionType.EXIT_LONG))
    factory = lambda: ScheduledContractFixture(actions)
    costs = ResearchCostModel(D(".001"), D(".002"), D(".001"))
    result = validate_out_of_sample(plan(dataset(changes={55: D(110), 63: D(90)}),
        strategy=factory(), costs=costs), factory, as_of=AS_OF)
    assert result.in_sample.completed_trades[0].net_pnl == D("9.37002")
    assert result.out_of_sample.completed_trades[0].net_pnl == D("-10.57002")
    rows = {row.metric: row for row in result.comparison}
    assert (rows["total_fees"].in_sample, rows["total_fees"].out_of_sample,
            rows["total_fees"].oos_minus_is) == (D(".20998"), D(".19002"), D("-.01996"))
    for field in ("total_spread_cost", "total_slippage_cost"):
        assert (rows[field].in_sample, rows[field].out_of_sample, rows[field].oos_minus_is) == (D(".21"), D(".19"), D("-.02"))
    assert rows["net_return"].oos_minus_is == D("-.001994004")


def test_strategy_configuration_change_during_is_is_rejected_before_oos():
    calls = []
    class MutatingFixture(ScheduledContractFixture):
        def decide(self, observation):
            calls.append(observation.candle.open_time)
            result = super().decide(observation)
            self.definition = StrategyDefinition("TEST_OOS", "1.0.0", {"fixture": "changed"})
            return result
    with pytest.raises(ValueError, match="configuration"):
        validate_out_of_sample(plan(strategy=MutatingFixture()), MutatingFixture, as_of=AS_OF)
    assert calls and all(timestamp < at(60) for timestamp in calls)
