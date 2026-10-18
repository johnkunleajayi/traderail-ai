"""Manually checkable next-open research fills; no network, database or profitability."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import UUID
import subprocess
import sys
import pytest
from app.market_intelligence.types import Candle, DatasetContext, TIMEFRAME_SECONDS
from app.strategies.configuration import StrategyDefinition
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.inputs import PositionState
from app.strategy_lab.costs import ResearchCostModel, COST_MODEL_VERSION
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.replay import replay_dataset
from app.strategy_lab.execution import simulate_execution, HistoricalExecutionConfiguration
from app.strategy_lab.execution.pricing import price_fill

D = Decimal
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
AS_OF = START + timedelta(days=500)
CONTEXT = DatasetContext(UUID(int=1), "BTC/USDT", "BINANCE", "1h")
ZERO = ResearchCostModel(D(0), D(0), D(0))
COSTS = ResearchCostModel(D(".001"), D(".02"), D(".005"))


def make_data(count=58, *, timeframe="1h"):
    context = replace(CONTEXT, timeframe=timeframe)
    duration = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    rows = tuple(Candle(context.instrument_id, context.provider, timeframe,
        START + duration*i, START + duration*(i+1) - timedelta(milliseconds=1),
        D(100+i), D(102+i), D(99+i), D(101+i), D(10), START+duration*(i+1)+timedelta(days=1))
        for i in range(count))
    return create_dataset(context, rows, warmup_start=rows[0].open_time,
        evaluation_start=rows[52].open_time, evaluation_end=rows[-1].open_time+duration, as_of=AS_OF)


class ScriptedStrategy:
    """Test harness, not a production strategy or a sizing policy."""
    def __init__(self, actions):
        self.actions = tuple(actions)
        self.definition = StrategyDefinition("TEST_EXECUTION", "1.0.0", {})
    def decide(self, observation):
        index = int((observation.candle.open_time-START) / timedelta(seconds=TIMEFRAME_SECONDS[observation.context.timeframe])) - 52
        action = self.actions[index] if index < len(self.actions) else DecisionType.NO_ACTION
        return StrategyDecision(observation, action, ("TEST_ONLY",), {"close":observation.candle.close})


def replay(data, actions=(), state=PositionState.CASH):
    return replay_dataset(data, ScriptedStrategy(actions), as_of=AS_OF, initial_state=state)


def simulate(data=None, actions=(), *, costs=COSTS, quantity=D(2), state=PositionState.CASH):
    data = data or make_data()
    result = replay(data, actions, state)
    return simulate_execution(data, result, quantity=quantity, cost_model=costs, as_of=AS_OF)


@pytest.mark.parametrize("action,state,sign", [
    (DecisionType.ENTER_LONG, PositionState.CASH, 1),
    (DecisionType.EXIT_LONG, PositionState.LONG, -1),
    (DecisionType.ENTER_SHORT, PositionState.CASH, -1),
    (DecisionType.EXIT_SHORT, PositionState.SHORT, 1)])
def test_all_four_actions_exact_adverse_formulas(action, state, sign):
    # Reference 153; full spread .02 => half 1.53; slip .005 => .765.
    result = simulate(actions=(action,), state=state)
    fill = result.fills[0]
    assert len(result.fills) == 1
    assert fill.action is action and fill.position_before is state
    assert fill.pricing.reference_open == D(153)
    assert fill.pricing.spread_adjustment == D("1.53")*sign
    assert fill.pricing.slippage_adjustment == D(".765")*sign
    expected = D("155.295") if sign == 1 else D("150.705")
    assert fill.pricing.fill_price == expected
    assert fill.pricing.reference_notional == D(306)
    assert fill.pricing.fill_notional == D(2)*expected
    assert fill.pricing.fee_amount == D(2)*expected*D(".001")
    assert fill.pricing.spread_cost == D("3.06")
    assert fill.pricing.slippage_cost == D("1.530")


def test_no_action_has_no_fill_no_cost_and_preserves_state():
    for state in PositionState:
        result = simulate(state=state)
        assert result.fills == result.unfilled_decisions == ()
        assert result.no_action_count == 6
        assert result.final_filled_state is state


def test_next_open_not_same_close_and_causal_timestamps():
    data = make_data()
    rows = list(data.candles)
    rows[53] = replace(rows[53], open=D("153.75"))
    data = create_dataset(data.context, rows, warmup_start=data.warmup_start,
        evaluation_start=data.evaluation_start, evaluation_end=data.evaluation_end, as_of=AS_OF)
    original = replay(data, (DecisionType.ENTER_LONG,))
    result = simulate_execution(data, original, quantity=D(1), cost_model=ZERO, as_of=AS_OF)
    fill = result.fills[0]
    assert fill.decision_timestamp == data.candles[52].close_time
    assert fill.eligible_fill_timestamp == data.candles[53].open_time
    assert fill.eligible_fill_timestamp > fill.decision_timestamp
    assert fill.pricing.fill_price == data.candles[53].open
    assert fill.pricing.fill_price == D("153.75")
    assert fill.pricing.fill_price != data.candles[52].close
    # Changing T's close alone cannot change the distinct next-open reference.
    rows = list(data.candles); rows[52] = replace(rows[52], close=D("152.25"))
    changed = create_dataset(data.context, rows, warmup_start=data.warmup_start,
        evaluation_start=data.evaluation_start, evaluation_end=data.evaluation_end, as_of=AS_OF)
    assert simulate(changed, (DecisionType.ENTER_LONG,), costs=ZERO).fills[0].pricing.fill_price == D("153.75")


@pytest.mark.parametrize("additional", [0, 1, 2, 4])
def test_execution_delay_is_additional_whole_candles(additional):
    costs = replace(ZERO, execution_delay=timedelta(hours=additional))
    data = make_data(60)
    result = simulate(data, (DecisionType.ENTER_LONG,), costs=costs)
    fill = result.fills[0]
    assert fill.configuration.delay_candles == additional
    assert fill.eligible_fill_timestamp == data.candles[53+additional].open_time
    assert fill.pricing.fill_price == data.candles[53+additional].open


@pytest.mark.parametrize("timeframe,seconds", list(TIMEFRAME_SECONDS.items()))
def test_delay_bridge_uses_selected_timeframe(timeframe, seconds):
    costs = replace(ZERO, execution_delay=timedelta(seconds=seconds*2))
    result = simulate(make_data(timeframe=timeframe), (DecisionType.ENTER_LONG,), costs=costs)
    assert result.configuration.delay_candles == 2
    assert result.fills[0].eligible_fill_timestamp == START+timedelta(seconds=seconds*55)


@pytest.mark.parametrize("delay", [timedelta(microseconds=1), timedelta(minutes=30), timedelta(hours=1,microseconds=1)])
def test_fractional_candle_delays_rejected(delay):
    with pytest.raises(ValueError, match="whole number"):
        simulate(actions=(DecisionType.ENTER_LONG,), costs=replace(ZERO, execution_delay=delay))


@pytest.mark.parametrize("delay", [timedelta(seconds=-1), -1, 0, True, D(1), 1.5])
def test_invalid_or_negative_delay_rejected_by_existing_cost_model(delay):
    with pytest.raises(ValueError): replace(ZERO, execution_delay=delay)


def test_final_decision_unfilled_does_not_mutate_filled_state():
    data = make_data(53)
    source = replay(data, (DecisionType.ENTER_LONG,))
    assert source.final_state is PositionState.LONG
    result = simulate_execution(data, source, quantity=D(1), cost_model=ZERO, as_of=AS_OF)
    assert result.fills == ()
    assert result.final_filled_state is PositionState.CASH
    pending = result.unfilled_decisions[0]
    assert pending.reason_code == "END_OF_DATASET"
    assert pending.eligible_fill_timestamp == data.evaluation_end
    assert pending.filled_state is PositionState.CASH
    assert pending.decision == source.records[0].decision


def test_final_exit_unfilled_retains_existing_filled_thesis():
    result = simulate(make_data(53), (DecisionType.EXIT_SHORT,), costs=ZERO, state=PositionState.SHORT)
    assert not result.fills
    assert result.final_filled_state is PositionState.SHORT


def test_delayed_pending_entry_and_exit_do_not_mutate_final_state():
    costs = replace(ZERO, execution_delay=timedelta(hours=2))
    result = simulate(make_data(54), (DecisionType.ENTER_LONG, DecisionType.EXIT_LONG), costs=costs)
    assert result.fills == () and len(result.unfilled_decisions) == 2
    assert result.final_filled_state is PositionState.CASH
    assert all(item.filled_state is PositionState.CASH for item in result.unfilled_decisions)
    assert result.unfilled_decisions[1].replay_thesis_state_before is PositionState.LONG


def test_available_entry_then_pending_exit_preserves_long_filled_state():
    costs = replace(ZERO, execution_delay=timedelta(hours=1))
    result = simulate(make_data(55), (DecisionType.ENTER_LONG, DecisionType.EXIT_LONG), costs=costs)
    assert len(result.fills) == len(result.unfilled_decisions) == 1
    assert result.final_filled_state is PositionState.LONG


def test_round_trip_and_short_actions_are_ordered_without_automatic_reversal():
    actions = (DecisionType.ENTER_LONG, DecisionType.EXIT_LONG,
        DecisionType.ENTER_SHORT, DecisionType.EXIT_SHORT)
    result = simulate(make_data(58), actions, costs=replace(ZERO, execution_delay=timedelta(hours=1)))
    assert [fill.action for fill in result.fills] == list(actions)
    assert [fill.position_before for fill in result.fills] == [PositionState.CASH, PositionState.LONG,
        PositionState.CASH, PositionState.SHORT]
    assert result.final_filled_state is PositionState.CASH
    assert [fill.eligible_fill_timestamp for fill in result.fills] == sorted(fill.eligible_fill_timestamp for fill in result.fills)


def test_fee_is_separate_and_uses_adjusted_notional():
    without = simulate(actions=(DecisionType.ENTER_LONG,), costs=replace(COSTS,trading_fee_rate=D(0))).fills[0]
    with_fee = simulate(actions=(DecisionType.ENTER_LONG,)).fills[0]
    assert with_fee.pricing.fill_price == without.pricing.fill_price
    assert with_fee.pricing.fee_amount == D(".310590")
    assert without.pricing.fee_amount == 0
    assert with_fee.pricing.fee_amount != D(306)*COSTS.trading_fee_rate
    assert with_fee.configuration.configuration()["fee_basis"] == "absolute_quantity_times_adjusted_fill_price_in_quote_currency"


@pytest.mark.parametrize("action", [DecisionType.ENTER_LONG, DecisionType.EXIT_LONG, DecisionType.ENTER_SHORT, DecisionType.EXIT_SHORT])
def test_zero_costs_equal_reference_open(action):
    price = price_fill(D("123.456789"), action, D(".00125"), ZERO)
    assert price.fill_price == price.reference_open
    assert price.spread_adjustment == price.slippage_adjustment == price.fee_amount == 0


@pytest.mark.parametrize("action,sign", [(DecisionType.ENTER_LONG,1),(DecisionType.EXIT_SHORT,1),
    (DecisionType.ENTER_SHORT,-1),(DecisionType.EXIT_LONG,-1)])
@pytest.mark.parametrize("spread,slip", [(D(".02"),D(0)), (D(0),D(".01"))])
def test_spread_and_slippage_individually_never_improve_fill(action, sign, spread, slip):
    price = price_fill(D(100), action, D(1), ResearchCostModel(D(0),spread,slip))
    assert price.fill_price == D(100)+D(sign)
    assert price.spread_cost + price.slippage_cost == D(1)


@pytest.mark.parametrize("quantity", [D(0),D(-1),D("NaN"),D("Infinity"),1,1.0,"1",None,True])
def test_invalid_quantity_rejected(quantity):
    with pytest.raises(ValueError): simulate(quantity=quantity)


@pytest.mark.parametrize("reference", [D(0),D(-1),D("NaN"),1.0])
def test_invalid_reference_rejected(reference):
    with pytest.raises(ValueError): price_fill(reference, DecisionType.ENTER_LONG,D(1),ZERO)


def test_adverse_costs_that_eliminate_positive_sell_price_rejected_at_exact_boundary():
    for slip in (D(".5"),D(".50000000000000000000000000001")):
        with pytest.raises(ValueError,match="non-positive"):
            price_fill(D(100),DecisionType.ENTER_SHORT,D(1),ResearchCostModel(D(0),D(1),slip))
    result = price_fill(D(100),DecisionType.ENTER_SHORT,D(1),ResearchCostModel(D(0),D(1),D(".49999999999999999999999999999")))
    assert result.fill_price == D(".000000000000000000000000001")


def test_no_action_cannot_be_directly_priced():
    with pytest.raises(ValueError): price_fill(D(100),DecisionType.NO_ACTION,D(1),ZERO)


def test_exact_decimal_arithmetic_independent_of_ambient_context():
    reference = D("12345678901234567890123456789.123456789")
    quantity = D(".000000000000123456789123456789")
    first = price_fill(reference,DecisionType.ENTER_LONG,quantity,COSTS)
    with localcontext() as ctx:
        ctx.prec=6
        second=price_fill(reference,DecisionType.ENTER_LONG,quantity,COSTS)
    assert first == second
    tiny=price_fill(D(".000000000000000000000001"),DecisionType.ENTER_LONG,D(".00001"),COSTS)
    assert tiny.fill_price == D(".000000000000000000000001015")


def test_repeated_simulation_identical_and_immutable():
    data=make_data()
    source=replay(data,(DecisionType.ENTER_LONG,DecisionType.EXIT_LONG))
    first=simulate_execution(data,source,quantity=D(2),cost_model=COSTS,as_of=AS_OF)
    with localcontext() as ctx:
        ctx.prec=8
        second=simulate_execution(data,source,quantity=D("2.000"),cost_model=replace(COSTS,spread_rate=D(".0200")),as_of=AS_OF)
    assert first == second
    with pytest.raises(FrozenInstanceError): first.final_filled_state=PositionState.SHORT
    with pytest.raises(FrozenInstanceError): first.fills[0].pricing.fill_price=D(0)
    with pytest.raises(FrozenInstanceError): first.configuration.delay_candles=2


def test_append_future_candles_keeps_already_available_fills_identical():
    actions=(DecisionType.ENTER_LONG,DecisionType.EXIT_LONG,DecisionType.ENTER_SHORT)
    early=simulate(make_data(55),actions,costs=ZERO)
    later=simulate(make_data(59),actions,costs=ZERO)
    assert len(early.fills)==2 and len(early.unfilled_decisions)==1
    assert early.fills == later.fills[:2]
    assert early.dataset_fingerprint != later.dataset_fingerprint
    assert early.simulation_identity != later.simulation_identity


def test_exact_provenance_and_configuration_identity():
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,))
    result=simulate_execution(data,source,quantity=D(2),cost_model=COSTS,as_of=AS_OF)
    fill=result.fills[0]
    assert result.dataset_fingerprint==data.dataset_fingerprint
    assert result.replay_identity==source.replay_identity
    assert fill.decision.decision_identity==source.records[0].decision.decision_identity
    assert fill.decision_record_identity==source.records[0].record_identity
    assert fill.strategy_configuration_identity==source.strategy_definition.configuration_identity
    assert fill.decision_timestamp==source.records[0].close_time
    assert fill.configuration.cost_model==COSTS
    assert fill.configuration.configuration()["cost_model"]["cost_model_version"]==COST_MODEL_VERSION
    assert fill.configuration.configuration()["execution_version"]=="1.0.0"
    assert fill.configuration.delay_candles==0
    assert fill.dataset_prefix_fingerprint != source.records[0].dataset_prefix_fingerprint
    assert all(value.startswith("sha256:") for value in (fill.fill_identity,result.simulation_identity,
        fill.configuration.configuration_identity, fill.configuration.cost_model_identity))


@pytest.mark.parametrize("change", ["fee", "spread", "slippage", "delay", "quantity"])
def test_meaningful_research_assumptions_change_identity(change):
    costs=COSTS; quantity=D(2)
    if change=="quantity": quantity=D(3)
    else:
        key,value={"fee":("trading_fee_rate",D(".002")),"spread":("spread_rate",D(".03")),
            "slippage":("slippage_rate",D(".006")),"delay":("execution_delay",timedelta(hours=1))}[change]
        costs=replace(COSTS,**{key:value})
    first=simulate(actions=(DecisionType.ENTER_LONG,))
    second=simulate(actions=(DecisionType.ENTER_LONG,),costs=costs,quantity=quantity)
    assert first.simulation_identity!=second.simulation_identity
    assert first.fills[0].fill_identity!=second.fills[0].fill_identity


@pytest.mark.parametrize("damage",["gap","bad_ohlc","nonpositive","mixed","outside","reversed","incomplete"])
def test_corrupt_or_incomplete_frozen_price_data_rejected(damage):
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,)); rows=list(data.candles)
    as_of=AS_OF
    if damage=="gap": rows.pop(53)
    if damage=="bad_ohlc": rows[53]=replace(rows[53],low=D(999))
    if damage=="nonpositive": rows[53]=replace(rows[53],open=D(0))
    if damage=="mixed": rows[53]=replace(rows[53],provider="OTHER")
    if damage=="outside": rows=rows[:-1]
    if damage=="reversed": rows.reverse()
    if damage=="incomplete": as_of=rows[-1].close_time
    with pytest.raises(ValueError):
        simulate_execution(replace(data,candles=tuple(rows)),source,quantity=D(1),cost_model=ZERO,as_of=as_of)


@pytest.mark.parametrize("damage",["timestamp","context","before","after","missing_record","duplicate_record", "missing_decision",
    "feature_identity","prefix_identity","record_identity","replay_identity","initial_state","final_state","regime"])
def test_tampered_replay_rejected_before_fills(damage):
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,)); record=source.records[0]
    changes={"timestamp":{"close_time":record.close_time+timedelta(seconds=1)},
        "context":{"context":replace(CONTEXT,canonical_symbol="ETH/USDT")},
        "before":{"position_before":PositionState.LONG}, "after":{"position_after":PositionState.CASH},
        "missing_decision":{"decision":None},"feature_identity":{"intelligence_identity":"forged"},
        "prefix_identity":{"dataset_prefix_fingerprint":"forged"},"record_identity":{"record_identity":"forged"},
        "regime":{"regime":None}}
    if damage in changes: source=replace(source,records=(replace(record,**changes[damage]),*source.records[1:]))
    elif damage=="missing_record": source=replace(source,records=source.records[:-1])
    elif damage=="duplicate_record": source=replace(source,records=(record,record,*source.records[2:]))
    elif damage=="replay_identity": source=replace(source,replay_identity="forged")
    elif damage=="initial_state": source=replace(source,initial_state=PositionState.SHORT)
    elif damage=="final_state": source=replace(source,final_state=PositionState.CASH)
    with pytest.raises(ValueError): simulate_execution(data,source,quantity=D(1),cost_model=ZERO,as_of=AS_OF)


def test_illegal_duplicate_entry_no_reversal_and_exit_without_position_rejected():
    for actions in ((DecisionType.EXIT_LONG,), (DecisionType.ENTER_LONG,DecisionType.ENTER_LONG),
                    (DecisionType.ENTER_LONG,DecisionType.ENTER_SHORT)):
        with pytest.raises(ValueError): simulate(actions=actions)


def test_full_dataset_identity_must_match_even_when_candle_values_are_valid():
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,))
    rows=list(data.candles); rows[-1]=replace(rows[-1],volume=D(999))
    changed=create_dataset(data.context,rows,warmup_start=data.warmup_start,
        evaluation_start=data.evaluation_start,evaluation_end=data.evaluation_end,as_of=AS_OF)
    with pytest.raises(ValueError,match="do not match"):
        simulate_execution(changed,source,quantity=D(1),cost_model=ZERO,as_of=AS_OF)


def test_no_indicator_regime_strategy_recalculation_during_simulation(monkeypatch):
    from app.market_intelligence import engine as intelligence
    from app.market_regimes import engine as regimes
    from app.strategies import contract
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,))
    def forbidden(*args,**kwargs): raise AssertionError("Must consume existing replay records only")
    monkeypatch.setattr(intelligence,"calculate_features",forbidden)
    monkeypatch.setattr(regimes,"classify_regimes",forbidden)
    monkeypatch.setattr(contract,"evaluate_strategy",forbidden)
    assert len(simulate_execution(data,source,quantity=D(1),cost_model=ZERO,as_of=AS_OF).fills)==1


def test_pricing_uses_only_open_not_favorable_high_low_or_close():
    # Pricing primitive intentionally accepts no high/low/close or current provider data.
    import inspect
    assert list(inspect.signature(price_fill).parameters)==["reference_open","action","quantity","model"]
    assert price_fill(D(100),DecisionType.ENTER_LONG,D(1),COSTS).fill_price==D("101.5")


def test_no_db_network_or_provider_imports_in_fresh_process():
    code="""
import sys
from app.strategy_lab.execution import simulate_execution
assert not any(any(n == x or n.startswith(x + '.') for x in
    ('sqlalchemy','fastapi','httpx','requests','app.db','app.market_data','app.core.config')) for n in sys.modules)
"""
    result=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_last_available_completed_candle_is_eligible_but_next_boundary_is_unfilled():
    data=make_data(54)
    available=simulate(data,(DecisionType.ENTER_LONG,),costs=ZERO)
    unavailable=simulate(data,(DecisionType.ENTER_LONG,),costs=replace(ZERO,execution_delay=timedelta(hours=1)))
    assert available.fills[0].eligible_fill_timestamp==data.candles[-1].open_time
    assert not available.unfilled_decisions
    assert not unavailable.fills
    assert unavailable.unfilled_decisions[0].eligible_fill_timestamp==data.evaluation_end


def test_inputs_remain_unmodified_by_simulation():
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,DecisionType.EXIT_LONG))
    before=repr((data,source,COSTS))
    simulate_execution(data,source,quantity=D(2),cost_model=COSTS,as_of=AS_OF)
    assert repr((data,source,COSTS))==before


def test_future_shifted_analytical_row_rejected_even_with_a_real_strategy_decision():
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,))
    first=source.records[0]
    shifted=replace(first,intelligence=source.records[1].intelligence)
    with pytest.raises(ValueError,match="timestamp|close|match"):
        simulate_execution(data,replace(source,records=(shifted,*source.records[1:])),quantity=D(1),cost_model=ZERO,as_of=AS_OF)


def test_extreme_delay_reports_controlled_timestamp_error():
    costs=replace(ZERO,execution_delay=timedelta(days=999999999))
    with pytest.raises(ValueError,match="representable"):
        simulate(actions=(DecisionType.ENTER_LONG,),costs=costs)


@pytest.mark.parametrize("damage",["counts","evidence","cost_model","cutoff"])
def test_malformed_simulation_inputs_fail_with_controlled_validation(damage):
    data=make_data(); source=replay(data,(DecisionType.ENTER_LONG,)); costs=ZERO; cutoff=AS_OF
    if damage=="counts": source=replace(source,warmup_count=52.0)
    if damage=="evidence":
        first=source.records[0]
        source=replace(source,records=(replace(first,regime=replace(first.regime,evidence=None)),*source.records[1:]))
    if damage=="cost_model": costs=None
    if damage=="cutoff": cutoff=AS_OF.replace(tzinfo=None)
    with pytest.raises(ValueError): simulate_execution(data,source,quantity=D(1),cost_model=costs,as_of=cutoff)
