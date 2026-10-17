"""Chronological research definitions tested without execution, providers or database."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from uuid import UUID
import json
import pytest
from app.market_intelligence.types import Candle, DatasetContext
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.validation import TimeWindow, ValidationSplit, ValidationPlan, ValidationKind
from app.strategy_lab.benchmarks import BenchmarkDefinition, BenchmarkConfiguration, BenchmarkKind
from app.strategy_lab.lifecycle import StrategyLifecycle, StrategyState
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.research import ResearchConfiguration, create_research_experiment
from app.strategy_lab.experiment import create_experiment, create_manifest, snapshot_configuration

D=Decimal
START=datetime(2025,1,1,tzinfo=timezone.utc)
CONTEXT=DatasetContext(UUID(int=1),"BTC/USDT","BINANCE","1h")


def at(hour):
    return START+timedelta(hours=hour)


def window(start,end):
    return TimeWindow(at(start),at(end))


def dataset(changed_oos=False):
    rows=[Candle(CONTEXT.instrument_id,"BINANCE","1h",at(i),at(i+1)-timedelta(milliseconds=1),
        D("100"),D("105"),D("95"),D("101"),D("1"),at(i+1)) for i in range(14)]
    if changed_oos:
        rows[-1]=replace(rows[-1],close=D("104"))
    return create_dataset(CONTEXT,rows,warmup_start=at(0),evaluation_start=at(2),
        evaluation_end=at(14),as_of=at(20))


def oos_plan():
    return ValidationPlan(ValidationKind.OUT_OF_SAMPLE,(ValidationSplit(window(2,8),window(8,14)),))


def walk_forward():
    return ValidationPlan(ValidationKind.WALK_FORWARD,(
        ValidationSplit(window(2,5),window(5,8)),ValidationSplit(window(8,11),window(11,14))))


def research(plan=None,benchmark_kinds=(BenchmarkKind.CASH,BenchmarkKind.BUY_AND_HOLD),cost=None):
    return ResearchConfiguration(plan or oos_plan(),
        BenchmarkConfiguration(tuple(BenchmarkDefinition(kind) for kind in benchmark_kinds)),
        cost or ResearchCostModel(D(".001"),D(".002"),D(".003")))


def experiment(configuration=None,**overrides):
    args={"research":configuration or research(),"strategy_configuration":{"a":1,"nested":{"x":D(".1"),"b":True}},
          "created_at":at(20)}
    args.update(overrides)
    return create_research_experiment(dataset(),**args)


def test_in_sample_definition_and_explicit_warmup_exclusion():
    plan=ValidationPlan(ValidationKind.IN_SAMPLE,(ValidationSplit(window(2,14)),))
    plan.validate_for(dataset())
    assert plan.evaluation_windows==(window(2,14),)
    assert plan.splits[0].configuration_cutoff==at(14)
    assert len(plan.in_sample_candles(dataset()))==12
    assert plan.out_of_sample_candles(dataset())==()
    assert all(row.open_time>=at(2) for row in plan.in_sample_candles(dataset()))


def test_out_of_sample_chronology_and_no_overlap():
    plan=oos_plan();data=dataset()
    assert plan.splits[0].configuration_cutoff==at(8)
    assert plan.evaluation_windows==(window(8,14),)
    in_sample=plan.in_sample_candles(data);oos=plan.out_of_sample_candles(data)
    assert tuple(row.open_time for row in in_sample)==tuple(at(i) for i in range(2,8))
    assert tuple(row.open_time for row in oos)==tuple(at(i) for i in range(8,14))
    assert not {row.open_time for row in in_sample}&{row.open_time for row in oos}
    assert all(row.close_time<plan.splits[0].configuration_cutoff for row in in_sample)


def test_future_oos_change_cannot_change_earlier_in_sample_view():
    original=dataset();future=dataset(changed_oos=True);plan=oos_plan()
    assert plan.in_sample_candles(original)==plan.in_sample_candles(future)
    assert plan.out_of_sample_candles(original)!=plan.out_of_sample_candles(future)
    assert original.dataset_fingerprint!=future.dataset_fingerprint
    assert plan.configuration()==oos_plan().configuration()


def test_walk_forward_disjoint_progressing_windows():
    plan=walk_forward();data=dataset();plan.validate_for(data)
    assert plan.evaluation_windows==(window(5,8),window(11,14))
    assert [row.configuration_cutoff for row in plan.splits]==[at(5),at(11)]
    seen=set()
    for index in range(2):
        for rows in (plan.in_sample_candles(data,index),plan.out_of_sample_candles(data,index)):
            times={row.open_time for row in rows}
            assert not times&seen
            seen|=times
    assert seen=={at(i) for i in range(2,14)}


@pytest.mark.parametrize("start,end",[(2,2),(4,2)])
def test_empty_and_reversed_windows_rejected(start,end):
    with pytest.raises(ValueError): window(start,end)


@pytest.mark.parametrize("start,end",[(START.replace(tzinfo=None),at(2)),(START,at(2).replace(tzinfo=None)),("bad",at(2))])
def test_invalid_timestamps_rejected(start,end):
    with pytest.raises(ValueError): TimeWindow(start,end)


@pytest.mark.parametrize("training,testing",[(window(2,8),window(7,14)),(window(8,14),window(2,8)),(window(2,14),window(5,7))])
def test_oos_overlap_reverse_and_containment_are_rejected(training,testing):
    with pytest.raises(ValueError): ValidationSplit(training,testing)


@pytest.mark.parametrize("kind,splits",[
    (ValidationKind.IN_SAMPLE,()),
    (ValidationKind.IN_SAMPLE,(ValidationSplit(window(2,8),window(8,14)),)),
    (ValidationKind.IN_SAMPLE,(ValidationSplit(window(2,8)),ValidationSplit(window(8,14)))),
    (ValidationKind.OUT_OF_SAMPLE,(ValidationSplit(window(2,14)),)),
    (ValidationKind.WALK_FORWARD,(ValidationSplit(window(2,8),window(8,14)),)),
    (ValidationKind.WALK_FORWARD,(ValidationSplit(window(2,8)),ValidationSplit(window(8,14)))),
    ("WALK_FORWARD",()),
])
def test_invalid_plan_shapes_rejected(kind,splits):
    with pytest.raises(ValueError): ValidationPlan(kind,splits)


@pytest.mark.parametrize("splits",[
    (ValidationSplit(window(2,5),window(5,8)),ValidationSplit(window(7,11),window(11,14))),
    (ValidationSplit(window(2,5),window(5,8)),ValidationSplit(window(2,11),window(11,14))),
    (ValidationSplit(window(8,11),window(11,14)),ValidationSplit(window(2,5),window(5,8))),
    (ValidationSplit(window(2,5),window(5,10)),ValidationSplit(window(8,11),window(11,14))),
])
def test_overlapping_or_unordered_walk_forward_rejected(splits):
    with pytest.raises(ValueError): ValidationPlan(ValidationKind.WALK_FORWARD,splits)


def test_explicit_unused_interval_is_allowed_not_silently_filled():
    plan=ValidationPlan(ValidationKind.OUT_OF_SAMPLE,(ValidationSplit(window(2,6),window(8,14)),))
    assert len(plan.in_sample_candles(dataset()))==4
    assert at(6) not in {row.open_time for row in plan.in_sample_candles(dataset())+plan.out_of_sample_candles(dataset())}
    assert plan.configuration()["splits"][0]["in_sample"]["end"]!=plan.configuration()["splits"][0]["out_of_sample"]["start"]


@pytest.mark.parametrize("period",[window(0,14),window(2,15),TimeWindow(at(2)+timedelta(minutes=30),at(14)),TimeWindow(at(2),at(13)+timedelta(minutes=30))])
def test_dataset_binding_rejects_warmup_outside_and_non_candle_boundaries(period):
    plan=ValidationPlan(ValidationKind.IN_SAMPLE,(ValidationSplit(period),))
    with pytest.raises(ValueError): plan.validate_for(dataset())
    with pytest.raises(ValueError): experiment(research(plan))


def test_definitions_are_deeply_immutable_and_timezone_canonical():
    source=list(walk_forward().splits)
    plan=ValidationPlan(ValidationKind.WALK_FORWARD,source)
    source.clear()
    assert len(plan.splits)==2
    with pytest.raises(FrozenInstanceError): plan.kind=ValidationKind.IN_SAMPLE
    with pytest.raises(FrozenInstanceError): plan.splits[0].in_sample.start=START
    zone=timezone(timedelta(hours=1))
    shifted=TimeWindow(at(2).astimezone(zone),at(8).astimezone(zone))
    assert shifted==window(2,8)
    assert shifted.configuration()==window(2,8).configuration()
    for index in (-1,2,True,"0"):
        with pytest.raises(ValueError): plan.in_sample_candles(dataset(),index)


def test_cash_and_buy_and_hold_are_comparisons_with_no_return_generation():
    cash=BenchmarkDefinition(BenchmarkKind.CASH)
    passive=BenchmarkDefinition(BenchmarkKind.BUY_AND_HOLD)
    assert cash.market_exposure==D("0")
    assert passive.market_exposure==D("1")
    assert isinstance(passive.market_exposure,D)
    assert cash.configuration()["period_source"]=="declared_validation_evaluation_windows"
    assert set(passive.configuration())=={"kind","market_exposure","period_source"}
    assert not hasattr(passive,"execute") and not hasattr(cash,"calculate_return")
    with pytest.raises(FrozenInstanceError): passive.kind=BenchmarkKind.CASH
    with pytest.raises(ValueError): BenchmarkDefinition("CASH")


def test_benchmark_membership_canonicalization_and_validation():
    defs=[BenchmarkDefinition(BenchmarkKind.CASH),BenchmarkDefinition(BenchmarkKind.BUY_AND_HOLD)]
    first=BenchmarkConfiguration(defs)
    second=BenchmarkConfiguration(list(reversed(defs)))
    assert first.configuration()==second.configuration()
    defs.clear(); assert len(first.definitions)==2
    for values in ([],[BenchmarkDefinition(BenchmarkKind.CASH)]*2,["CASH"],"CASH"):
        with pytest.raises(ValueError): BenchmarkConfiguration(values)


def test_exact_lifecycle_values_and_no_automatic_promotion():
    expected=["EXPERIMENTAL","BACKTESTED","VALIDATED","PAPER","LIVE_CANDIDATE","SHADOW","LIMITED_LIVE","LIVE","SUSPENDED","RETIRED"]
    assert [state.value for state in StrategyState]==expected
    for state in StrategyState:
        assert StrategyLifecycle(state).state is state
    lifecycle=StrategyLifecycle(StrategyState.EXPERIMENTAL)
    experiment();create_manifest(dataset(),experiment())
    assert lifecycle.state is StrategyState.EXPERIMENTAL
    assert not hasattr(lifecycle,"promote") and not hasattr(lifecycle,"transition")
    with pytest.raises(FrozenInstanceError): lifecycle.state=StrategyState.LIVE
    with pytest.raises(ValueError): StrategyLifecycle("LIVE")
    with pytest.raises(ValueError): StrategyState("READY")


def test_research_identity_delegates_to_unchanged_part1_snapshot_and_manifest():
    config=research();data=dataset()
    strategy={"a":1,"nested":{"x":D(".1"),"b":True}}
    wrapped=experiment(config)
    direct=create_experiment(data,strategy_configuration={"research":config.configuration(),"strategy":strategy},created_at=at(20))
    assert wrapped==direct
    manifest=create_manifest(data,wrapped)
    assert manifest.identity==wrapped.identity
    payload=json.loads(manifest.identity.strategy_configuration.canonical_payload)
    assert payload[0]=="map"
    assert '"validation"' in manifest.identity.strategy_configuration.canonical_payload
    assert '"benchmarks"' in manifest.identity.strategy_configuration.canonical_payload
    assert '"cost_model"' in manifest.identity.strategy_configuration.canonical_payload
    assert config.configuration()["benchmarks"]["evaluation_windows"]==[window(8,14).configuration()]
    assert research(walk_forward()).configuration()["benchmarks"]["evaluation_windows"]==[window(5,8).configuration(),window(11,14).configuration()]


def test_meaningful_cost_validation_and_benchmark_changes_alter_identity():
    original=experiment().experiment_id
    changes=[research(cost=ResearchCostModel(D(".002"),D(".002"),D(".003"))),
        research(cost=ResearchCostModel(D(".001"),D(".003"),D(".003"))),
        research(cost=ResearchCostModel(D(".001"),D(".002"),D(".004"))),
        research(cost=ResearchCostModel(D(".001"),D(".002"),D(".003"),timedelta(seconds=1))),
        research(ValidationPlan(ValidationKind.OUT_OF_SAMPLE,(ValidationSplit(window(2,7),window(7,14)),))),
        research(ValidationPlan(ValidationKind.IN_SAMPLE,(ValidationSplit(window(2,14)),))),
        research(walk_forward()),research(benchmark_kinds=(BenchmarkKind.CASH,)),
        research(benchmark_kinds=(BenchmarkKind.BUY_AND_HOLD,))]
    identities=[experiment(config).experiment_id for config in changes]
    assert original not in identities
    assert len(set(identities))==len(identities)


def test_dictionary_order_numeric_scale_and_created_timestamps_do_not_change_identity():
    first=experiment()
    second=experiment(research(benchmark_kinds=(BenchmarkKind.BUY_AND_HOLD,BenchmarkKind.CASH),
        cost=ResearchCostModel(D(".0010"),D(".0020"),D(".0030"))),
        strategy_configuration={"nested":{"b":True,"x":D(".10")},"a":1},created_at=at(21))
    assert first.identity==second.identity
    assert create_manifest(dataset(),first).created_at!=create_manifest(dataset(),second).created_at
    config=research().configuration()
    reordered={key:config[key] for key in reversed(config)}
    assert snapshot_configuration(config)==snapshot_configuration(reordered)


def test_repeated_execution_and_detached_configuration_do_not_mutate_inputs():
    config=research();data=dataset()
    first=experiment(config);again=experiment(config)
    assert first==again
    mapping=config.configuration();mapping["cost_model"]["trading_fee_rate"]=D(".5")
    mapping["validation"]["splits"].clear()
    assert experiment(config)==first and data==dataset()
    assert config.configuration()["cost_model"]["trading_fee_rate"]==D(".001")
    with pytest.raises(FrozenInstanceError): config.cost_model=ResearchCostModel(D("0"),D("0"),D("0"))
    assert first.identity.market_intelligence_version==first.identity.regime_engine_version=="1.0.0"


def test_invalid_integration_inputs_rejected():
    for field in ("validation","benchmarks","cost_model"):
        args={"validation":oos_plan(),"benchmarks":research().benchmarks,"cost_model":research().cost_model}
        args[field]=None
        with pytest.raises(ValueError): ResearchConfiguration(**args)
    with pytest.raises(ValueError): create_research_experiment(dataset(),research=None,strategy_configuration={},created_at=at(20))
    with pytest.raises(ValueError): experiment(strategy_configuration=[])


def test_window_chronology_uses_utc_even_during_repeated_local_hour():
    class RepeatedHour(tzinfo):
        def utcoffset(self, dt):
            return timedelta(hours=0 if dt.fold else 1)
        def dst(self, dt):
            return timedelta(0)
    zone=RepeatedHour()
    later=datetime(2025,1,1,1,0,tzinfo=zone,fold=1)  # 01:00 UTC
    earlier=datetime(2025,1,1,1,30,tzinfo=zone,fold=0)  # 00:30 UTC
    with pytest.raises(ValueError): TimeWindow(later,earlier)
    assert TimeWindow(earlier,later).end-TimeWindow(earlier,later).start==timedelta(minutes=30)
