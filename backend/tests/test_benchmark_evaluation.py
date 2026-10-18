"""Literal passive oracles, exact OOS binding and deterministic benchmark evidence."""
from dataclasses import replace, FrozenInstanceError
from datetime import timedelta
from decimal import localcontext
import pytest
from app.strategy_lab.benchmarks import BenchmarkConfiguration, BenchmarkDefinition, BenchmarkKind
from app.strategy_lab.benchmark_evaluation import evaluate_benchmarks
from app.strategy_lab.benchmark_evaluation.engine import _benchmark
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.out_of_sample import validate_out_of_sample
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from test_out_of_sample_validation import dataset, plan, flat_result, AS_OF, D, at

CONFIG = BenchmarkConfiguration(tuple(BenchmarkDefinition(kind) for kind in BenchmarkKind))
BUY = BenchmarkDefinition(BenchmarkKind.BUY_AND_HOLD)
CASH = BenchmarkDefinition(BenchmarkKind.CASH)


def test_cash_exact_no_market_exposure_cost_or_trade():
    row = _benchmark(plan(), CASH, as_of=AS_OF)
    assert row.initial_capital == row.ending_equity == row.residual_cash == D(10000)
    assert row.total_return == row.maximum_drawdown == row.exposure_percentage == row.turnover == 0
    assert row.quantity == row.costs.total == 0
    assert row.entry_time is row.fill_price is row.reference_open is None
    assert all(point.equity == 10000 for point in row.observations)


@pytest.mark.parametrize("last,equity,ret,drawdown", [
    (D(120),D(12000),D('.2'),D(0)), (D(80),D(8000),D('-.2'),D('.2')),
    (D(100),D(10000),D(0),D(0))])
def test_passive_rising_falling_flat_literal_oracles(last,equity,ret,drawdown):
    row = _benchmark(plan(dataset(changes={67:last})), BUY, as_of=AS_OF)
    assert row.quantity == 100 and row.entry_time == at(60)
    assert row.reference_open == row.fill_price == 100
    assert row.ending_equity == equity and row.total_return == ret
    assert row.maximum_drawdown == drawdown
    assert row.exposure_percentage == 100 and row.turnover == 1
    assert row.costs.total == 0


def test_fee_inclusive_budget_literal_oracle_and_no_cost_double_count():
    # Entry 100 + half-spread 1 + slippage 1 = 102; fee/unit 1.02.
    # Capital 10302 buys exactly 100 units; final close 110 => equity 11000.
    costs = ResearchCostModel(D('.01'),D('.02'),D('.01'))
    row = _benchmark(plan(dataset(changes={67:D(110)}), costs=costs, capital=D(10302)), BUY, as_of=AS_OF)
    assert row.quantity == 100 and row.residual_cash == 0
    assert row.reference_open == 100 and row.fill_price == 102
    assert row.costs.fees == 102 and row.costs.spread_cost == row.costs.slippage_cost == 100
    assert row.ending_equity == 11000
    assert row.ending_equity-row.initial_capital == 1000-row.costs.total == 698


def test_rounding_dust_never_borrows_even_under_low_ambient_precision():
    costs = ResearchCostModel(D('.003'),D('.002'),D('.001'))
    baseline = _benchmark(plan(costs=costs), BUY, as_of=AS_OF)
    with localcontext() as context:
        context.prec=6
        assert _benchmark(plan(costs=costs), BUY, as_of=AS_OF) == baseline
    assert baseline.residual_cash >= 0
    assert baseline.quantity > 0


def test_one_eligible_candle_and_explicit_delay():
    row = _benchmark(plan(dataset(count=61)), BUY, as_of=AS_OF)
    assert len(row.observations) == 1 and row.entry_time == at(60)
    assert row.exposure_percentage == 100
    costs=ResearchCostModel(D(0),D(0),D(0),timedelta(hours=1))
    delayed = _benchmark(plan(costs=costs), BUY, as_of=AS_OF)
    assert delayed.entry_time == at(61) and delayed.exposure_percentage == D('87.5')
    with pytest.raises(ValueError, match="Insufficient OOS"):
        _benchmark(plan(dataset(count=61),costs=costs),BUY,as_of=AS_OF)


def test_fair_comparisons_bind_real_strategy_and_preserve_all_definitions():
    result=flat_result()
    rows=evaluate_benchmarks(result,CONFIG,as_of=AS_OF)
    assert len(rows)==2
    for row in rows:
        assert row.strategy_result_identity == result.out_of_sample.result_identity
        assert row.benchmark.dataset_identity == result.plan.dataset_identity
        assert row.benchmark.canonical_symbol == 'BTC/USDT' and row.benchmark.quote_currency == 'USDT'
        assert dict(row.differences)['total_return']==0
        assert dict(row.differences)['exposure_percentage'] == (0 if row.benchmark.kind is BenchmarkKind.CASH else -100)
    assert rows == evaluate_benchmarks(result,CONFIG,as_of=AS_OF)
    assert rows == evaluate_benchmarks(result,BenchmarkConfiguration(tuple(reversed(CONFIG.definitions))),as_of=AS_OF)


@pytest.mark.parametrize('field,value',[('initial_capital',D(1)),('dataset_fingerprint','wrong')])
def test_foreign_strategy_data_or_capital_rejected(field,value):
    result=flat_result()
    corrupted=replace(result,out_of_sample=replace(result.out_of_sample,**{field:value}))
    with pytest.raises(ValueError):
        evaluate_benchmarks(corrupted,CONFIG,as_of=AS_OF)


def test_foreign_window_rejected():
    result=flat_result()
    replay=replace(result.out_of_sample.replay,evaluation_start=at(61))
    with pytest.raises(ValueError):
        evaluate_benchmarks(replace(result,out_of_sample=replace(result.out_of_sample,replay=replay)),CONFIG,as_of=AS_OF)


def test_invalid_price_and_malformed_window_rejected_without_repair():
    good=plan()
    candle=replace(good.dataset.candles[60],open=D(0))
    bad=replace(good.dataset,candles=good.dataset.candles[:60]+(candle,)+good.dataset.candles[61:])
    with pytest.raises(ValueError):
        _benchmark(replace(good,dataset=bad),BUY,as_of=AS_OF)
    from app.strategy_lab.validation import TimeWindow
    with pytest.raises(ValueError):
        TimeWindow(at(61),at(60))


def test_benchmark_id_changes_with_costs_and_is_immutable():
    base=_benchmark(plan(),BUY,as_of=AS_OF)
    changed=_benchmark(plan(costs=ResearchCostModel(D('.001'),D(0),D(0))),BUY,as_of=AS_OF)
    assert base.result_identity != changed.result_identity
    with pytest.raises(FrozenInstanceError):
        base.quantity=D(0)


def test_walk_forward_benchmarks_keep_independent_windows():
    from test_walk_forward_validation import flat_result as wf_result
    result=wf_result()
    rows=evaluate_benchmarks(result,CONFIG,as_of=AS_OF+timedelta(days=200))
    assert len(rows)==4 and {row.fold_index for row in rows}=={0,1}
    assert len({row.benchmark.window_identity for row in rows})==2
    assert all(row.benchmark.initial_capital==10000 for row in rows)


def test_future_candles_cannot_change_previous_passive_observations():
    early=_benchmark(plan(dataset(count=67)),BUY,as_of=AS_OF)
    later=_benchmark(plan(dataset(changes={67:D(120)})),BUY,as_of=AS_OF)
    assert later.observations[:len(early.observations)] == early.observations
    assert early.quantity == later.quantity and early.costs == later.costs


def test_mismatched_execution_assumptions_rejected():
    result=flat_result()
    foreign=replace(result.plan,execution_configuration=replace(result.plan.execution_configuration,
        cost_model=ResearchCostModel(D('.01'),D(0),D(0))))
    with pytest.raises(ValueError,match='share'):
        evaluate_benchmarks(replace(result,plan=foreign),CONFIG,as_of=AS_OF)


def test_equivalent_decimal_costs_do_not_change_benchmark_identity():
    first=_benchmark(plan(costs=ResearchCostModel(D('.001'),D(0),D(0))),BUY,as_of=AS_OF)
    second=_benchmark(plan(costs=ResearchCostModel(D('.00100'),D('0.0'),D('0.00'))),BUY,as_of=AS_OF)
    assert first==second
    assert first.engine_version=='1.0.0'
    assert first.context==plan().dataset.context
    assert first.window==plan().split.out_of_sample


def test_empty_and_incomplete_snapshot_fail_without_provider_fallback():
    good=plan()
    with pytest.raises(ValueError):
        _benchmark(replace(good,dataset=replace(good.dataset,candles=())),BUY,as_of=AS_OF)
    with pytest.raises(ValueError):
        _benchmark(good,BUY,as_of=at(60))
