"""Disjoint validation through unchanged analytics, Adaptive Trend and accounting."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import pytest
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.inputs import PositionState
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.out_of_sample import OutOfSamplePlan
from app.strategy_lab.validation import TimeWindow, ValidationSplit
from app.strategy_lab.walk_forward import WalkForwardPlan, validate_walk_forward, summarize_folds
from test_out_of_sample_validation import dataset, plan as paired_plan, AS_OF, D, at
from test_integrated_9c_audit import frozen_dataset


def join_folds(datasets, changes=None):
    changes = changes or {}
    rows, folds = [], []
    offset = timedelta(0)
    for data in datasets:
        shifted = []
        for row in data.candles:
            candle = replace(row, open_time=row.open_time+offset, close_time=row.close_time+offset,
                retrieved_at=row.retrieved_at+offset)
            index = len(rows)+len(shifted)
            if index in changes:
                price = changes[index]
                candle = replace(candle, open=price, high=price+D(1), low=price-D(1), close=price)
            shifted.append(candle)
        local = create_dataset(data.context, tuple(shifted), warmup_start=data.warmup_start+offset,
            evaluation_start=data.evaluation_start+offset, evaluation_end=data.evaluation_end+offset,
            as_of=AS_OF+timedelta(days=200))
        split_time = local.evaluation_start + timedelta(hours=2 if len(local.warmup_candles)==128 else 8)
        folded = paired_plan(data) if len(data.warmup_candles)==52 else None
        if folded:
            fold = replace(folded, dataset=local, warmup=TimeWindow(local.warmup_start, local.evaluation_start),
                split=ValidationSplit(TimeWindow(local.evaluation_start, split_time), TimeWindow(split_time, local.evaluation_end)))
        else:
            template = paired_plan()
            fold = OutOfSamplePlan(local, TimeWindow(local.warmup_start, local.evaluation_start),
                ValidationSplit(TimeWindow(local.evaluation_start, split_time), TimeWindow(split_time, local.evaluation_end)),
                template.strategy_definition, template.execution_configuration, D(1), D(10000))
        folds.append(fold); rows.extend(shifted)
        offset += data.evaluation_end-data.warmup_start
    parent = create_dataset(datasets[0].context, tuple(rows), warmup_start=rows[0].open_time,
        evaluation_start=folds[0].split.in_sample.start, evaluation_end=folds[-1].dataset.evaluation_end,
        as_of=AS_OF+timedelta(days=200))
    return WalkForwardPlan(parent, tuple(folds))


def flat_plan(counts=(68, 68), changes=None):
    return join_folds(tuple(dataset(count=count) for count in counts), changes)


@lru_cache(None)
def flat_result():
    return validate_walk_forward(flat_plan(), AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=200))


def test_one_fold_supported_without_changing_9a_minimum_fold_rule():
    result = validate_walk_forward(flat_plan((68,)), AdaptiveTrendStrategy, as_of=AS_OF)
    assert result.summary.fold_count == 1
    assert result.folds[0].index == 0
    assert result.folds[0].validation.out_of_sample.performance.trade_count == 0
    assert result.summary.mean_net_return == result.summary.median_net_return == 0


def test_multiple_folds_are_independent_and_do_not_stitch_equity():
    result = flat_result()
    assert len(result.folds) == result.summary.fold_count == 2
    for fold in result.folds:
        research = fold.validation.out_of_sample
        assert research.replay.initial_state is PositionState.CASH
        assert research.initial_capital == D(10000)
        assert len(research.equity_curve) == 8
        assert research.replay.strategy_definition == AdaptiveTrendStrategy().definition
        assert research.result_identity and fold.fold_identity
        assert research.dataset_fingerprint == research.replay.dataset_fingerprint
        assert fold.validation.plan.dataset_identity == result.plan.folds[fold.index].dataset.dataset_fingerprint
        assert research.execution.configuration == fold.validation.plan.execution_configuration
    assert "equity_curve" not in {item.name for item in fields(type(result))}
    assert result.summary.zero_trade_folds == result.summary.breakeven_folds == 2
    assert result.summary.mean_expectancy is result.summary.mean_profit_factor is None
    assert result.summary.defined_expectancy_folds == result.summary.defined_profit_factor_folds == 0
    assert result.summary.return_sign_consistency == "ZERO"


def test_losing_fold_with_real_unchanged_adaptive_trend_and_zero_trade_fold():
    p = join_folds((frozen_dataset("long_loss"), frozen_dataset("no_trades")))
    result = validate_walk_forward(p, AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=200))
    first, second = (row.validation.out_of_sample for row in result.folds)
    assert first.completed_trades[0].net_pnl == D(-3)
    assert first.performance.net_return == D("-.0003")
    assert second.performance.trade_count == 0
    assert result.summary.losing_folds == result.summary.zero_trade_folds == result.summary.breakeven_folds == 1
    assert result.summary.mean_net_return == result.summary.median_net_return == D("-.00015")
    assert result.summary.worst_net_return == D("-.0003") and result.summary.best_net_return == 0
    assert result.summary.return_sign_consistency == "MIXED"


def test_open_position_and_pending_fill_cannot_cross_fold_boundary():
    p = join_folds((frozen_dataset("open_long"), frozen_dataset("unfilled"), dataset()))
    result = validate_walk_forward(p, AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=200))
    assert result.folds[0].validation.out_of_sample.open_position.direction is PositionState.LONG
    assert result.folds[1].validation.out_of_sample.open_position is None
    assert len(result.folds[1].validation.out_of_sample.execution.unfilled_decisions) == 1
    assert not result.folds[2].validation.out_of_sample.execution.fills
    assert all(row.validation.out_of_sample.replay.initial_state is PositionState.CASH for row in result.folds)


def test_summary_matches_independent_literal_arithmetic():
    empty = flat_result().folds[0].validation.out_of_sample.performance
    metrics = (replace(empty, net_return=D(".10"), maximum_drawdown=D(".02"), trade_count=2,
                       expectancy=D(3), profit_factor=D(2), total_fees=D(1)),
               replace(empty, net_return=D("-.04"), maximum_drawdown=D(".08"), trade_count=1,
                       expectancy=D(-2), profit_factor=D(".5"), total_spread_cost=D(2)),
               replace(empty, net_return=D(0), maximum_drawdown=D(".01"), total_slippage_cost=D(3)),
               replace(empty, net_return=None, maximum_drawdown=None))
    summary = summarize_folds(metrics)
    assert (summary.profitable_folds, summary.losing_folds, summary.breakeven_folds, summary.undefined_return_folds) == (1,1,1,1)
    assert summary.mean_net_return == D(".02") and summary.median_net_return == 0
    assert summary.worst_net_return == D("-.04") and summary.best_net_return == D(".10")
    assert summary.median_maximum_drawdown == D(".02") and summary.worst_maximum_drawdown == D(".08")
    assert summary.total_completed_trades == 3 and summary.zero_trade_folds == 2
    assert summary.mean_expectancy == D(".5") and summary.defined_expectancy_folds == 2
    assert summary.mean_profit_factor == D("1.25") and summary.defined_profit_factor_folds == 2
    assert (summary.total_fees, summary.total_spread_cost, summary.total_slippage_cost) == (D(1),D(2),D(3))
    assert summary.return_sign_consistency == "MIXED"


def test_even_median_undefined_statistics_and_low_decimal_context():
    empty = flat_result().folds[0].validation.out_of_sample.performance
    with localcontext() as context:
        context.prec = 3
        summary = summarize_folds((replace(empty, net_return=D(".123456789")), replace(empty, net_return=D(".123456791"))))
    assert summary.median_net_return == summary.mean_net_return == D(".123456790")
    undefined = summarize_folds((replace(empty, net_return=None, maximum_drawdown=None),))
    assert undefined.mean_net_return is undefined.median_net_return is undefined.worst_net_return is None
    assert undefined.median_maximum_drawdown is undefined.worst_maximum_drawdown is None
    assert undefined.mean_expectancy is undefined.mean_profit_factor is undefined.return_sign_consistency is None
    with pytest.raises(ValueError): summarize_folds(())


def test_future_data_changes_full_identity_but_not_earlier_fold_result():
    original = flat_result()
    changed = validate_walk_forward(flat_plan(changes={135: D(101)}), AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=200))
    assert changed.folds[0] == original.folds[0]
    assert changed.folds[1].validation.out_of_sample.replay.records[:-1] == original.folds[1].validation.out_of_sample.replay.records[:-1]
    assert changed.validation_identity != original.validation_identity
    assert changed.plan.plan_identity != original.plan.plan_identity


def test_appending_later_fold_history_cannot_change_earlier_results():
    extended = validate_walk_forward(flat_plan((68,70)), AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=200))
    original = flat_result()
    assert extended.folds[0] == original.folds[0]
    assert extended.folds[1].validation.out_of_sample.replay.records[:8] == original.folds[1].validation.out_of_sample.replay.records
    assert extended.folds[1].validation.out_of_sample.equity_curve[:8] == original.folds[1].validation.out_of_sample.equity_curve


def test_repeated_runs_and_timestamp_independence():
    result = validate_walk_forward(flat_plan(), AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=201))
    assert result == flat_result()
    assert result.validation_identity == flat_result().validation_identity
    with pytest.raises(FrozenInstanceError): result.summary.fold_count = 1
    with pytest.raises(FrozenInstanceError): result.plan.folds = ()


@pytest.mark.parametrize("order", [(1,0), (0,0), (1,1)])
def test_unordered_or_overlapping_oos_and_history_rejected(order):
    original = flat_plan()
    with pytest.raises(ValueError): replace(original, folds=tuple(original.folds[i] for i in order))


def test_overlapping_expanding_context_is_rejected():
    original = flat_plan()
    data = original.dataset
    expanded = OutOfSamplePlan(data, original.folds[0].warmup,
        ValidationSplit(TimeWindow(data.evaluation_start, original.folds[1].split.out_of_sample.start), original.folds[1].split.out_of_sample),
        original.folds[0].strategy_definition, original.folds[0].execution_configuration, D(1), D(10000))
    with pytest.raises(ValueError, match="overlapping"): replace(original, folds=(original.folds[0], expanded))


def test_parent_boundary_and_content_mismatches_rejected():
    original = flat_plan()
    shorter = create_dataset(original.dataset.context, original.dataset.candles[:-1],
        warmup_start=original.dataset.warmup_start, evaluation_start=original.dataset.evaluation_start,
        evaluation_end=original.dataset.evaluation_end-timedelta(hours=1), as_of=AS_OF+timedelta(days=200))
    with pytest.raises(ValueError, match="outside"): replace(original, dataset=shorter)
    changed = flat_plan(changes={135: D(101)})
    with pytest.raises(ValueError, match="slice"): replace(original, dataset=changed.dataset)
    with pytest.raises(ValueError, match="fingerprint"): replace(original, dataset=replace(original.dataset, dataset_fingerprint="forged"))


def test_insufficient_warmup_uses_unchanged_oos_guard():
    with pytest.raises(ValueError, match="warm-up"): paired_plan(dataset(warmup=10))


@pytest.mark.parametrize("field,value", [("quantity", D(2)), ("initial_capital", D(20000))])
def test_assumption_changes_must_be_uniform_and_change_identity(field, value):
    original = flat_plan()
    with pytest.raises(ValueError, match="assumptions"):
        replace(original, folds=(original.folds[0], replace(original.folds[1], **{field:value})))
    changed = replace(original, folds=tuple(replace(fold, **{field:value}) for fold in original.folds))
    assert changed.plan_identity != original.plan_identity
    result = validate_walk_forward(changed, AdaptiveTrendStrategy, as_of=AS_OF+timedelta(days=200))
    assert result.validation_identity != flat_result().validation_identity


def test_strategy_instances_cannot_be_reused_across_folds():
    instances = [AdaptiveTrendStrategy(), AdaptiveTrendStrategy()]
    counter = []
    def factory():
        instance = instances[len(counter)%2]; counter.append(True); return instance
    with pytest.raises(ValueError, match="fresh"): validate_walk_forward(flat_plan(), factory, as_of=AS_OF+timedelta(days=200))


def test_no_database_network_api_or_promotion_dependency():
    script = """import sys
from app.strategy_lab.walk_forward import WalkForwardPlan, validate_walk_forward
for prefix in ('sqlalchemy','psycopg','httpx','fastapi','app.db','app.market_data','app.lifecycle'):
    assert not any(name==prefix or name.startswith(prefix+'.') for name in sys.modules),prefix
"""
    result = subprocess.run([sys.executable,"-c",script], cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr


def test_profitable_and_losing_actual_adaptive_folds_have_literal_equal_fold_return():
    result = validate_walk_forward(join_folds((frozen_dataset("long_profit"),frozen_dataset("long_loss"))),
        AdaptiveTrendStrategy,as_of=AS_OF+timedelta(days=200))
    assert (result.summary.profitable_folds,result.summary.losing_folds,result.summary.breakeven_folds) == (1,1,0)
    assert result.summary.mean_net_return == result.summary.median_net_return == D(".0007")
    assert result.summary.best_net_return == D(".0017") and result.summary.worst_net_return == D("-.0003")
    assert result.summary.total_completed_trades == 2


def test_nonzero_costs_are_aggregated_from_oos_only_with_literal_oracle():
    from app.strategy_lab.costs import ResearchCostModel
    from app.strategy_lab.execution.config import HistoricalExecutionConfiguration
    original = join_folds((frozen_dataset("long_loss"),))
    config = HistoricalExecutionConfiguration(ResearchCostModel(D(".001"),D(".002"),D(".001")),"1h")
    p = replace(original,folds=tuple(replace(fold,execution_configuration=config) for fold in original.folds))
    result = validate_walk_forward(p,AdaptiveTrendStrategy,as_of=AS_OF+timedelta(days=200))
    assert result.summary.total_fees == D(".351006")
    assert result.summary.total_spread_cost == result.summary.total_slippage_cost == D(".351")
    assert result.summary.mean_net_return == D("-.0004053006")
    assert result.folds[0].validation.in_sample.incurred_costs.total == 0


@pytest.mark.parametrize("field,value",[("net_return",D("NaN")),("profit_factor",D("Infinity")),
    ("total_fees",D(-1)),("trade_count",-1)])
def test_invalid_fold_metrics_are_rejected_instead_of_becoming_summary_evidence(field,value):
    source = flat_result().folds[0].validation.out_of_sample.performance
    with pytest.raises(ValueError): summarize_folds((replace(source,**{field:value}),))


def test_mutable_parent_candle_container_is_rejected():
    original = flat_plan()
    with pytest.raises(ValueError,match="frozen"):
        replace(original,dataset=replace(original.dataset,candles=list(original.dataset.candles)))


def test_caller_fold_list_is_detached_into_immutable_plan():
    original = flat_plan()
    supplied = list(original.folds)
    result = WalkForwardPlan(original.dataset,supplied)
    supplied.clear()
    assert result.folds == original.folds and isinstance(result.folds,tuple)
    assert result.plan_identity == original.plan_identity
