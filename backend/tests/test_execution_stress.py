"""Research assumptions only; real unchanged Adaptive Trend and shared validation."""
from dataclasses import replace, FrozenInstanceError, fields
from datetime import timedelta
from functools import lru_cache
import pytest
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.stress_testing import StressPlan, StressScenario, evaluate_stress
from app.strategy_lab.numeric import difference, exact_sum
from test_out_of_sample_validation import plan, AS_OF, D, ZERO_COST
from test_walk_forward_validation import join_folds
from test_integrated_9c_audit import frozen_dataset


def scenario(code,fee='0',spread='0',slippage='0',delay=0):
    return StressScenario(code,ResearchCostModel(D(fee),D(spread),D(slippage),timedelta(hours=delay)))


def declared():
    return (scenario('BASELINE'),scenario('HIGHER_FEES',fee='.001'),
        scenario('WIDER_SPREAD',spread='.002'),scenario('HIGHER_SLIPPAGE',slippage='.001'),
        scenario('DELAY',delay=1),scenario('COMBINED',fee='.001',spread='.002',slippage='.001'))


def research(name='long_loss'):
    return join_folds((frozen_dataset(name),)).folds[0]


@lru_cache(None)
def actual():
    return evaluate_stress(StressPlan(research(),declared()),as_of=AS_OF)


def test_all_explicit_scenarios_retain_assumptions_and_metrics_without_ranking():
    result=actual()
    assert len(result.scenarios)==6
    assert [row.scenario.code for row in result.scenarios] == sorted(row.code for row in declared())
    assert result.baseline.scenario.code=='BASELINE'
    assert not {'best','selected','rank','promoted'} & {field.name for field in fields(result)}
    for row in result.scenarios:
        assert row.validation.plan.quantity == result.plan.validation.quantity == 1
        assert row.validation.plan.strategy_definition == result.plan.validation.strategy_definition
        assert row.oos_metrics == (row.validation.out_of_sample.performance,)
        assert row.validation.plan.execution_configuration.cost_model == row.scenario.costs
        assert len(row.differences)==1
        assert 'net_return' in dict(row.differences[0].metric_changes)
    assert result.baseline.oos_metrics[0].trade_count==1
    combined=next(row for row in result.scenarios if row.scenario.code=='COMBINED')
    trade=combined.validation.out_of_sample.completed_trades[0]
    assert trade.gross_pnl == -3 and trade.net_pnl==D('-4.053006')
    assert trade.costs.fees==D('.351006') and trade.costs.spread_cost==trade.costs.slippage_cost==D('.351')
    assert difference(trade.gross_pnl,trade.costs.total)==trade.net_pnl
    assert (trade.exit_fill_price-trade.entry_fill_price)*trade.quantity-trade.costs.fees==trade.net_pnl


def test_repeated_and_reordered_scenarios_produce_identical_results():
    original=actual()
    reordered=StressPlan(research(),tuple(reversed(declared())))
    assert reordered == original.plan
    assert evaluate_stress(reordered,as_of=AS_OF)==original


def test_delay_is_next_open_plus_delay_and_never_closes_open_positions():
    result=evaluate_stress(StressPlan(research('open_long'),(scenario('BASELINE'),scenario('DELAY',delay=1))),as_of=AS_OF)
    base=result.baseline.validation.out_of_sample
    delayed=next(row for row in result.scenarios if row.scenario.code=='DELAY').validation.out_of_sample
    assert base.open_position is not None and not base.completed_trades
    assert delayed.open_position is None and not delayed.execution.fills
    assert delayed.execution.unfilled_decisions
    assert not delayed.completed_trades
    executed=actual()
    delayed=next(row for row in executed.scenarios if row.scenario.code=='DELAY').validation.out_of_sample
    assert delayed.execution.fills[0].pricing.reference_open == research().dataset.candles[132].open


def test_costs_cannot_resize_or_borrow_unaffordable_quantity():
    frozen=replace(research('open_long'),initial_capital=D(177))
    # Baseline entry costs exactly 177; fee-stressed entry exceeds available capital.
    with pytest.raises(ValueError,match='[Cc]apital|[Cc]ash|afford'):
        evaluate_stress(StressPlan(frozen,(scenario('BASELINE'),scenario('FEE',fee='.01'))),as_of=AS_OF)
    assert frozen.quantity==1 and frozen.initial_capital==177


@pytest.mark.parametrize('scenarios', [(),(scenario('OTHER'),),
    (scenario('BASELINE'),scenario('BASELINE')),(scenario('BASELINE',fee='.01'),)])
def test_baseline_required_unique_and_matches_original(scenarios):
    with pytest.raises(ValueError):
        StressPlan(plan(),scenarios)


def test_fractional_delay_rejected_before_running_any_strategy():
    bad=StressScenario('FRACTION',ResearchCostModel(D(0),D(0),D(0),timedelta(minutes=30)))
    with pytest.raises(ValueError,match='whole number'):
        StressPlan(plan(),(scenario('BASELINE'),bad))


def test_equivalent_decimals_same_identity_and_config_is_frozen():
    assert scenario('FEE',fee='.001')==scenario('FEE',fee='.00100')
    baseline=AdaptiveTrendStrategy().definition
    original=StressPlan(plan(),declared())
    with pytest.raises(FrozenInstanceError):
        original.scenarios[0].costs.trading_fee_rate=D(1)
    assert AdaptiveTrendStrategy().definition==baseline
    assert original.validation.strategy_definition==baseline
    assert StressPlan(plan(),(scenario('BASELINE'),scenario('FEE',fee='.01'))).plan_identity != original.plan_identity


def test_zero_trade_fold_and_walk_forward_independence():
    from test_walk_forward_validation import flat_plan
    result=evaluate_stress(StressPlan(flat_plan(),(scenario('BASELINE'),scenario('HIGH',fee='.1'))),as_of=AS_OF+timedelta(days=200))
    assert len(result.baseline.oos_metrics)==2
    assert all(metric.trade_count==0 for row in result.scenarios for metric in row.oos_metrics)
    assert all(metric.net_return==0 for row in result.scenarios for metric in row.oos_metrics)
    assert all(metric.expectancy is None for metric in result.baseline.oos_metrics)


@pytest.mark.parametrize('code,fees,spread,slippage',[
    ('BASELINE','0','0','0'),('HIGHER_FEES','.351','0','0'),
    ('WIDER_SPREAD','0','.351','0'),('HIGHER_SLIPPAGE','0','0','.351'),
    ('COMBINED','.351006','.351','.351')])
def test_isolated_and_combined_costs_exact_once(code,fees,spread,slippage):
    row=next(row for row in actual().scenarios if row.scenario.code==code)
    research_result=row.validation.out_of_sample
    trade=research_result.completed_trades[0]
    assert trade.costs.fees==D(fees) and trade.costs.spread_cost==D(spread) and trade.costs.slippage_cost==D(slippage)
    assert research_result.incurred_costs == trade.costs
    assert difference(trade.gross_pnl,trade.costs.total)==trade.net_pnl
    assert all(fill.quantity==1 for fill in research_result.execution.fills)
    assert all(point.cash>=0 for point in research_result.equity_curve)
    assert row.differences[0].total_cost_change==trade.costs.total


def test_appended_future_does_not_change_available_stress_fills_or_equity():
    prefix=evaluate_stress(StressPlan(research('open_long'),(scenario('BASELINE'),)),as_of=AS_OF)
    early=prefix.baseline.validation.out_of_sample
    later=actual().baseline.validation.out_of_sample
    assert early.execution.fills == later.execution.fills[:len(early.execution.fills)]
    assert early.equity_curve == later.equity_curve[:len(early.equity_curve)]
    assert early.replay.records == later.replay.records[:len(early.replay.records)]


def test_no_provider_database_or_optimization_dependency_in_new_modules():
    import ast
    from pathlib import Path
    root=Path(__file__).parents[1]/'app'/'strategy_lab'
    for directory in ('benchmark_evaluation','stress_testing'):
        for source in (root/directory).glob('*.py'):
            tree=ast.parse(source.read_text())
            modules=[node.module or '' for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
            modules += [alias.name for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names]
            assert not any(module.startswith(('sqlalchemy','psycopg','httpx','requests','app.db','app.market_data')) for module in modules)


def test_explicit_higher_cost_can_change_return_sign_without_selecting_scenario():
    result=evaluate_stress(StressPlan(research('small_winner'),
        (scenario('BASELINE'),scenario('HIGH_FEE',fee='.01'))),as_of=AS_OF)
    higher=next(row for row in result.scenarios if row.scenario.code=='HIGH_FEE')
    assert result.baseline.oos_metrics[0].net_return > 0
    assert higher.oos_metrics[0].net_return < 0
    assert higher.differences[0].return_sign_changed is True
    assert higher.differences[0].total_cost_change > 0
    assert result.baseline.validation.plan.strategy_definition == higher.validation.plan.strategy_definition
