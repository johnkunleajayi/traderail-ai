"""Pure strategy contracts tested with analytical fixtures, never actual strategy rules."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext
from uuid import UUID
from pathlib import Path
import subprocess
import sys
import pytest
from app.market_intelligence.types import Candle, DatasetContext, FeatureRow
from app.market_intelligence.engine import calculate_features
from app.market_regimes.engine import classify_regimes
from app.market_regimes.types import Regime
from app.strategies.configuration import StrategyDefinition
from app.strategies.inputs import StrategyInput
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.contract import Strategy, evaluate_strategy

D=Decimal
START=datetime(2025,1,1,tzinfo=timezone.utc)
AS_OF=START+timedelta(days=10)
CONTEXT=DatasetContext(UUID(int=1),"BTC/USDT","BINANCE","1h")


@pytest.fixture
def observation():
    candles=[Candle(CONTEXT.instrument_id,"BINANCE","1h",START+timedelta(hours=i),
        START+timedelta(hours=i+1,milliseconds=-1),D(100+i),D(102+i),D(99+i),
        D(101+i),D("1.00001"),START+timedelta(hours=i+1)) for i in range(100)]
    features=calculate_features(CONTEXT,candles,as_of=AS_OF)
    regimes=classify_regimes(features,as_of=AS_OF)
    return StrategyInput(CONTEXT,candles[-1],features[-1],regimes[-1],
        StrategyDefinition("TEST_ONLY","1.0.0",{"parameters":{"sample":D(".125"),"enabled":True},"labels":["a","b"]}),AS_OF)


def decision(observation,kind=DecisionType.NO_ACTION,**kwargs):
    args={"reason_codes":("NO_OPPORTUNITY",),"evidence":{"close":observation.candle.close}}
    args.update(kwargs)
    return StrategyDecision(observation,kind,**args)


def test_inputs_configuration_and_decisions_are_deeply_immutable(observation):
    with pytest.raises(FrozenInstanceError): observation.as_of=START
    with pytest.raises(FrozenInstanceError): observation.candle.close=D("0")
    with pytest.raises(TypeError): observation.definition.parameters["parameters"]["sample"]=D(".2")
    assert observation.definition.parameters["labels"]==("a","b")
    source={"nested":{"value":D(".2")},"items":[1,2]}
    definition=StrategyDefinition("TEST_ONLY","1",source)
    source["nested"]["value"]=D(".3");source["items"].clear()
    assert definition.parameters["nested"]["value"]==D(".2")
    assert definition.parameters["items"]==(1,2)
    evidence={"nested":{"rsi":D("55")},"observations":[D("1"),D("2")]}
    result=decision(observation,evidence=evidence)
    evidence["nested"]["rsi"]=D("10")
    assert result.evidence["nested"]["rsi"]==D("55")
    with pytest.raises(TypeError): result.evidence["nested"]["rsi"]=D("10")
    with pytest.raises(FrozenInstanceError): result.decision=DecisionType.ENTER_LONG


@pytest.mark.parametrize("kind",list(DecisionType))
def test_all_five_decisions_and_no_venue_capability_dependency(observation,kind):
    result=decision(observation,kind)
    assert result.decision is kind
    assert result.timestamp==observation.candle.close_time
    assert result.timestamp.tzinfo==timezone.utc
    assert result.configuration_identity==observation.definition.configuration_identity
    assert result.strategy_family=="TEST_ONLY" and result.strategy_version=="1.0.0"
    assert result.reason_codes==("NO_OPPORTUNITY",)
    assert result.evidence["close"]==observation.candle.close


def test_exact_decision_enum_no_hold_or_order_statuses():
    assert [value.value for value in DecisionType]==["ENTER_LONG","EXIT_LONG","ENTER_SHORT","EXIT_SHORT","NO_ACTION"]
    with pytest.raises(ValueError): DecisionType("HOLD")


def test_configuration_identity_dictionary_order_and_numeric_types():
    first=StrategyDefinition("TEST_ONLY","1",{"b":{"y":D(".100"),"x":True},"a":3})
    second=StrategyDefinition("TEST_ONLY","1",{"a":3,"b":{"x":True,"y":D(".1")}})
    assert first.configuration_identity==second.configuration_identity
    for family,version,params in [("OTHER","1",first.parameters),("TEST_ONLY","2",first.parameters),
        ("TEST_ONLY","1",{"a":4,"b":{"x":True,"y":D(".1")}})]:
        assert StrategyDefinition(family,version,params).configuration_identity!=first.configuration_identity
    assert len({StrategyDefinition("TEST_ONLY","1",{"v":v}).configuration_identity for v in (1,True,"1",D("1"))})==4


@pytest.mark.parametrize("parameters",[None,[],{"v":.1},{"v":D("NaN")},{1:"invalid"},{"v":set()}])
def test_malformed_configuration_rejected(parameters):
    with pytest.raises(ValueError): StrategyDefinition("TEST_ONLY","1",parameters)


@pytest.mark.parametrize("family,version",[("","1"),("test family","1"),(None,"1"),("TEST_ONLY",""),("TEST_ONLY",None)])
def test_explicit_family_version_required(family,version):
    with pytest.raises(ValueError): StrategyDefinition(family,version,{})


@pytest.mark.parametrize("name,value",[("canonical_symbol","ETH/USDT"),("provider","OTHER"),("timeframe","4h")])
def test_analytical_context_mismatch_rejected(observation,name,value):
    with pytest.raises(ValueError): replace(observation,intelligence=replace(observation.intelligence,**{name:value}))
    with pytest.raises(ValueError): replace(observation,regime=replace(observation.regime,**{name:value}))


def test_candle_linkage_and_context_mismatch_rejected(observation):
    with pytest.raises(ValueError): replace(observation,candle=replace(observation.candle,instrument_id=UUID(int=2)))
    with pytest.raises(ValueError): replace(observation,context=replace(observation.context,canonical_symbol="ETH/USDT"))


@pytest.mark.parametrize("shift",[-1,1])
def test_timestamp_and_future_analytical_rows_rejected(observation,shift):
    for name in ("intelligence","regime"):
        row=getattr(observation,name)
        shifted=replace(row,open_time=row.open_time+timedelta(hours=shift),close_time=row.close_time+timedelta(hours=shift))
        with pytest.raises(ValueError): replace(observation,**{name:shifted})
    with pytest.raises(ValueError): replace(observation,regime=replace(observation.regime,close_time=None))
    with pytest.raises(ValueError): replace(observation,intelligence=replace(observation.intelligence,close_time=observation.intelligence.close_time.replace(tzinfo=None)))


def test_completion_and_retrieval_future_relationships_rejected(observation):
    with pytest.raises(ValueError): replace(observation,as_of=observation.candle.close_time)
    with pytest.raises(ValueError): replace(observation,candle=replace(observation.candle,retrieved_at=AS_OF+timedelta(seconds=1)))
    with pytest.raises(ValueError): replace(observation,as_of=AS_OF.replace(tzinfo=None))


@pytest.mark.parametrize("name",["close","ema_20","ema_50","rsi_14","atr_14","macd","macd_signal","macd_histogram","simple_return","log_return","realized_volatility"])
def test_incomplete_intelligence_rejected_even_for_no_action(observation,name):
    with pytest.raises(ValueError): replace(observation,intelligence=replace(observation.intelligence,**{name:None}))


@pytest.mark.parametrize("name,value",[("close",D("-1")),("rsi_14",D("101")),("atr_14",D("-1")),("realized_volatility",D("NaN")),("ema_20",1.0)])
def test_invalid_analytical_values_rejected(observation,name,value):
    with pytest.raises(ValueError): replace(observation,intelligence=replace(observation.intelligence,**{name:value}))


def test_candle_close_and_mirrored_regime_evidence_must_match(observation):
    with pytest.raises(ValueError): replace(observation,candle=replace(observation.candle,close=observation.candle.close-D("1")))
    with pytest.raises(ValueError): replace(observation,regime=replace(observation.regime,
        evidence=replace(observation.regime.evidence,rsi_14=D("1"))))
    with pytest.raises(ValueError): replace(observation,regime=None)
    with pytest.raises(ValueError): replace(observation,intelligence=None)
    with pytest.raises(ValueError): replace(observation,definition={})


@pytest.mark.parametrize("kind",[Regime.UNKNOWN,Regime.UNSAFE])
def test_complete_unknown_unsafe_are_valid_without_frontier_strategy_rules(observation,kind):
    adjusted=replace(observation,regime=replace(observation.regime,regime=kind))
    assert decision(adjusted).decision is DecisionType.NO_ACTION


def test_actual_engine_confirmation_streak_not_artificially_capped(observation):
    candles=[Candle(CONTEXT.instrument_id,"BINANCE","1h",START+timedelta(hours=i),
        START+timedelta(hours=i+1,milliseconds=-1),D("100"),D("101"),D("99"),D("100"),D("1"),
        START+timedelta(hours=i+1)) for i in range(100)]
    features=calculate_features(CONTEXT,candles,as_of=AS_OF)
    result=classify_regimes(features,as_of=AS_OF)[-1]
    assert result.evidence.confirmation_count>result.evidence.confirmation_required
    validated=StrategyInput(CONTEXT,candles[-1],features[-1],result,observation.definition,AS_OF)
    assert decision(validated).decision is DecisionType.NO_ACTION


@pytest.mark.parametrize("key",["order","quantity","notional","position_size","execution_authority","trade_intent","venue", "profit_probability","probability_of_profit","api_key","order_side"])
def test_decisions_and_parameters_cannot_carry_execution_fields_even_nested(observation,key):
    with pytest.raises(TypeError): StrategyDecision(observation,DecisionType.NO_ACTION,("NO_OPPORTUNITY",),{},**{key:True})
    with pytest.raises(ValueError): decision(observation,evidence={"nested":[{key:True}]})
    with pytest.raises(ValueError): StrategyDefinition("TEST_ONLY","1",{"nested":{key:True}})
    assert key not in {item.name for item in fields(StrategyDecision)}
    assert not hasattr(decision(observation),key)


@pytest.mark.parametrize("reasons",[(),("",),("bad code",),("SAME","SAME"),"NO_OPPORTUNITY"])
def test_reason_codes_explicit_valid_and_distinct(observation,reasons):
    with pytest.raises(ValueError): decision(observation,reason_codes=reasons)


def test_decision_identity_deterministic_and_sensitive_to_relevant_input_output(observation):
    original=decision(observation)
    assert original==decision(observation)
    assert original.decision_identity==decision(observation,evidence={"close":D("200.0000")}).decision_identity
    assert original.decision_identity!=decision(observation,DecisionType.ENTER_SHORT).decision_identity
    assert original.decision_identity!=decision(observation,reason_codes=("OTHER_REASON",)).decision_identity
    assert original.decision_identity!=decision(observation,evidence={"close":D("201")}).decision_identity
    other=replace(observation,definition=StrategyDefinition("TEST_ONLY","2",{}))
    assert other.input_identity!=observation.input_identity
    assert decision(other).decision_identity!=original.decision_identity
    assert decision(replace(observation,as_of=AS_OF+timedelta(days=1))).decision_identity==original.decision_identity
    with localcontext() as context:
        context.prec=3
        assert decision(observation).decision_identity==original.decision_identity


def test_timezone_equivalence_and_source_immutability(observation):
    before=(observation.candle,observation.intelligence,observation.regime)
    zone=timezone(timedelta(hours=1))
    candle=replace(observation.candle,open_time=observation.candle.open_time.astimezone(zone),
        close_time=observation.candle.close_time.astimezone(zone),retrieved_at=observation.candle.retrieved_at.astimezone(zone))
    adjusted=replace(observation,candle=candle,as_of=observation.as_of.astimezone(zone))
    assert adjusted.input_identity==observation.input_identity
    assert decision(adjusted).decision_identity==decision(observation).decision_identity
    assert before==(observation.candle,observation.intelligence,observation.regime)


class NoActionContractFixture:
    # Test-only unconditional fixture, not a product strategy or registered family.
    def __init__(self,definition): self.definition=definition
    def decide(self,observation): return decision(observation)


def test_protocol_and_single_decision_boundary_validation(observation):
    implementation=NoActionContractFixture(observation.definition)
    assert isinstance(implementation,Strategy)
    assert evaluate_strategy(implementation,observation)==evaluate_strategy(implementation,observation)
    with pytest.raises(ValueError): evaluate_strategy(NoActionContractFixture(StrategyDefinition("OTHER","1",{})),observation)
    class WrongOutput:
        definition=observation.definition
        def decide(self,input): return {"order":True}
    with pytest.raises(ValueError): evaluate_strategy(WrongOutput(),observation)
    class WrongInput:
        definition=observation.definition
        def decide(self,input): return decision(replace(input,definition=StrategyDefinition("OTHER","1",{})))
    with pytest.raises(ValueError): evaluate_strategy(WrongInput(),observation)


def test_fresh_process_import_has_no_provider_database_or_api_dependency():
    script="""import sys
from app.strategies.contract import Strategy, evaluate_strategy
from app.strategies.inputs import StrategyInput
from app.strategies.decisions import StrategyDecision
for prefix in ('httpx','sqlalchemy','fastapi','app.db','app.market_data','app.venues','app.auth','app.portfolios'):
    assert not any(name == prefix or name.startswith(prefix+'.') for name in sys.modules), prefix
"""
    result=subprocess.run([sys.executable,"-c",script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_retrieval_future_check_uses_utc_during_repeated_local_hour(observation):
    class RepeatedHour(tzinfo):
        def utcoffset(self,dt): return timedelta(hours=0 if dt.fold else 1)
        def dst(self,dt): return timedelta(0)
    zone=RepeatedHour()
    # Wall-clock retrieval 05:00 appears earlier than 05:30, but is later in UTC.
    boundary=datetime(2025,1,5,5,30,tzinfo=zone,fold=0)  # 04:30 UTC
    future=datetime(2025,1,5,5,0,tzinfo=zone,fold=1)  # 05:00 UTC
    candle=replace(observation.candle,
        open_time=(observation.candle.open_time+timedelta(hours=1)).replace(tzinfo=zone,fold=0),
        close_time=(observation.candle.close_time+timedelta(hours=1)).replace(tzinfo=zone,fold=0),
        retrieved_at=future)
    with pytest.raises(ValueError): replace(observation,candle=candle,as_of=boundary)
