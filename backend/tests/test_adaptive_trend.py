"""Intentional synthetic structures test hypotheses, not current BTC/ETH observations."""
from dataclasses import asdict, fields, FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import UUID
import subprocess
import sys
import pytest
from app.market_intelligence.types import Candle, DatasetContext, FeatureRow
from app.market_regimes.types import Evidence, Regime, RegimeResult
from app.market_regimes.config import REGIME_CONFIG
from app.strategies.inputs import PositionState, StrategyInput, STRATEGY_INPUT_FORMAT
from app.strategies.configuration import StrategyDefinition
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.contract import Strategy, evaluate_strategy
from app.strategies.adaptive_trend import AdaptiveTrendConfiguration, AdaptiveTrendStrategy

D=Decimal
START=datetime(2025,1,1,tzinfo=timezone.utc)
END=START+timedelta(hours=1,milliseconds=-1)
AS_OF=START+timedelta(hours=2)
CONTEXT=DatasetContext(UUID(int=1),"BTC/USDT","BINANCE","1h")


def observation(direction="long",state=PositionState.CASH,*,strategy=None,regime=None,
                feature_changes=None,evidence_changes=None):
    strategy=strategy or AdaptiveTrendStrategy()
    if direction=="long":
        fast,slow,rsi,macd,signal,hist,spread,fslope,sslope=map(D,("99","98","60","1",".5",".5",".01",".001",".0005"))
        kind=Regime.BULL_TREND
    elif direction=="short":
        fast,slow,rsi,macd,signal,hist,spread,fslope,sslope=map(D,("101","102","40","-1","-.5","-.5","-.01","-.001","-.0005"))
        kind=Regime.BEAR_TREND
    else:
        fast,slow,rsi,macd,signal,hist,spread,fslope,sslope=map(D,("100","100","50","0","0","0","0","0","0"))
        kind=Regime.RANGE
    feature=FeatureRow("BTC/USDT","BINANCE","1h",START,END,D("100"),fast,slow,rsi,D("1"),
        macd,signal,hist,D(".01"),D(".00995033"),D(".005"))
    feature=replace(feature,**(feature_changes or {}))
    selected=regime or kind
    evidence=Evidence(normalized_ema_spread=spread,ema20_slope=fslope,ema50_slope=sslope,
        rsi_14=feature.rsi_14,macd=feature.macd,macd_signal=feature.macd_signal,
        macd_histogram=feature.macd_histogram,normalized_macd_histogram=hist/D("100"),
        normalized_atr=D(".01"),realized_volatility=feature.realized_volatility,
        volatility_ratio=D("1"),atr_ratio=D("1"),candidate=selected,
        confirmation_count=3,confirmation_required=3,available_candles=100,candidate_history_required=53,
        reason_codes=("SYNTHETIC_STRUCTURE",))
    evidence=replace(evidence,**(evidence_changes or {}))
    result=RegimeResult("BTC/USDT","BINANCE","1h",START,END,selected,evidence,REGIME_CONFIG,"1.0.0")
    candle=Candle(CONTEXT.instrument_id,"BINANCE","1h",START,END,D("100"),D("120"),D("80"),
        feature.close,D("1"),START+timedelta(hours=1))
    return StrategyInput(CONTEXT,candle,feature,result,strategy.definition,AS_OF,state)


def decide(*args,strategy=None,**kwargs):
    strategy=strategy or AdaptiveTrendStrategy()
    return evaluate_strategy(strategy,observation(*args,strategy=strategy,**kwargs))


@pytest.mark.parametrize("direction,expected",[("long",DecisionType.ENTER_LONG),("short",DecisionType.ENTER_SHORT)])
def test_multiple_confirming_dimensions_enter_from_cash(direction,expected):
    result=decide(direction)
    assert result.decision is expected
    assert all(result.evidence["entry_confirmations"].values())
    assert result.strategy_family=="ADAPTIVE_TREND" and result.strategy_version=="1.0.0"
    assert result.reason_codes==("LONG_TREND_CONFIRMED" if direction=="long" else "SHORT_TREND_CONFIRMED",)


@pytest.mark.parametrize("kind",[Regime.RANGE,Regime.UNKNOWN,Regime.UNSAFE,Regime.HIGH_VOLATILITY,Regime.BREAKOUT])
def test_non_trend_regimes_no_entry_even_with_appealing_indicators(kind):
    result=decide("long",regime=kind)
    assert result.decision is DecisionType.NO_ACTION
    assert result.evidence["regime"]==kind.value


@pytest.mark.parametrize("state",list(PositionState))
@pytest.mark.parametrize("kind",[Regime.UNSAFE,Regime.HIGH_VOLATILITY])
def test_unsafe_high_volatility_never_make_directional_decisions(state,kind):
    assert decide("short",state,regime=kind).decision is DecisionType.NO_ACTION


@pytest.mark.parametrize("direction,state",[("long",PositionState.LONG),("short",PositionState.SHORT)])
def test_existing_matching_thesis_no_repeated_entry(direction,state):
    result=decide(direction,state)
    assert result.decision is DecisionType.NO_ACTION
    assert result.evidence["exit_confirmation_count"]==0


@pytest.mark.parametrize("direction,state,expected",[("short",PositionState.LONG,DecisionType.EXIT_LONG),
                                                    ("long",PositionState.SHORT,DecisionType.EXIT_SHORT)])
def test_confirmed_opposite_trend_invalidates_thesis_not_immediate_reversal(direction,state,expected):
    result=decide(direction,state)
    assert result.decision is expected
    assert "OPPOSITE_TREND_CONFIRMED" in result.reason_codes
    assert result.evidence["position_state"]==state.value


@pytest.mark.parametrize("direction,state,regime,expected",[
    ("short",PositionState.LONG,Regime.BULL_TREND,DecisionType.EXIT_LONG),
    ("long",PositionState.SHORT,Regime.BEAR_TREND,DecisionType.EXIT_SHORT)])
def test_multiple_dimensions_exit_deterioration_before_opposite_confirmation(direction,state,regime,expected):
    result=decide(direction,state,regime=regime,evidence_changes={"confirmation_count":1,
        "candidate":Regime.BEAR_TREND if state==PositionState.LONG else Regime.BULL_TREND,
        "retained_previous":True,"previous_regime":regime})
    assert result.decision is expected
    assert result.evidence["exit_confirmation_count"]==3
    assert "OPPOSITE_TREND_CONFIRMED" not in result.reason_codes
    assert {"EXIT_STRUCTURE_CONFIRMED","EXIT_SLOPES_CONFIRMED","EXIT_MOMENTUM_CONFIRMED"}<=set(result.reason_codes)


def test_conflicting_weak_momentum_no_entry():
    result=decide("long",feature_changes={"rsi_14":D("50"),"macd_signal":D("1.1"),"macd_histogram":D("-.1")},
        evidence_changes={"normalized_macd_histogram":D("-.001")})
    assert result.decision is DecisionType.NO_ACTION
    assert "MOMENTUM_NOT_CONFIRMED" in result.reason_codes


@pytest.mark.parametrize("direction,rsi",[("long",D("80")),("short",D("20"))])
def test_extreme_rsi_is_not_automatic_entry_or_profit_taking(direction,rsi):
    assert decide(direction,feature_changes={"rsi_14":rsi}).decision is DecisionType.NO_ACTION
    state=PositionState.LONG if direction=="long" else PositionState.SHORT
    assert decide(direction,state,feature_changes={"rsi_14":rsi}).decision is DecisionType.NO_ACTION


@pytest.mark.parametrize("change",[
    {"candidate":Regime.RANGE,"confirmation_count":1,"retained_previous":True},
    {"confirmation_count":2}, {"confirmation_required":4},
    {"candidate_history_required":101}, {"candidate":Regime.UNKNOWN},
])
def test_regime_label_alone_does_not_prove_confirmation(change):
    result=decide("long",evidence_changes=change)
    assert result.decision is DecisionType.NO_ACTION
    assert "REGIME_NOT_CONFIRMED" in result.reason_codes


@pytest.mark.parametrize("feature_change,evidence_change,reason",[
    ({"ema_20":D("97")},{},"EMA_STRUCTURE_NOT_CONFIRMED"),
    ({},{"normalized_ema_spread":D(".001")},"EMA_STRUCTURE_NOT_CONFIRMED"),
    ({},{"ema20_slope":D(".0001")},"EMA_SLOPES_NOT_CONFIRMED"),
    ({},{"ema50_slope":D("0")},"EMA_SLOPES_NOT_CONFIRMED"),
    ({"ema_20":D("101"),"ema_50":D("99")},{},"CLOSE_POSITION_NOT_CONFIRMED"),
    ({},{"normalized_macd_histogram":D(".00001")},"MOMENTUM_NOT_CONFIRMED"),
])
def test_independent_entry_dimensions_are_all_required(feature_change,evidence_change,reason):
    result=decide("long",feature_changes=feature_change,evidence_changes=evidence_change)
    assert result.decision is DecisionType.NO_ACTION
    assert reason in result.reason_codes


@pytest.mark.parametrize("feature_change,evidence_change",[
    ({},{"normalized_atr":D(".03")}),({"realized_volatility":D(".03")},{}),
    ({},{"volatility_ratio":D("2.01")}),({},{"atr_ratio":D("2.01")})])
def test_absolute_and_relative_volatility_filters(feature_change,evidence_change):
    result=decide("long",feature_changes=feature_change,evidence_changes=evidence_change)
    assert result.decision is DecisionType.NO_ACTION
    assert "VOLATILITY_TOO_HIGH" in result.reason_codes
    # Entry volatility filters alone do not act as a take-profit/risk exit policy.
    assert decide("long",PositionState.LONG,feature_changes=feature_change,evidence_changes=evidence_change).decision is DecisionType.NO_ACTION


def test_one_deterioration_dimension_does_not_force_exit():
    result=decide("long",PositionState.LONG,feature_changes={"rsi_14":D("45"),"macd":D("-1"),
        "macd_signal":D("-.5"),"macd_histogram":D("-.5")},evidence_changes={"normalized_macd_histogram":D("-.005")})
    assert result.decision is DecisionType.NO_ACTION
    assert result.evidence["exit_confirmation_count"]==1
    assert result.reason_codes==("LONG_EXIT_NOT_CONFIRMED",)


def test_unknown_range_and_breakout_normally_no_action_for_intact_thesis():
    for kind in (Regime.UNKNOWN,Regime.RANGE,Regime.BREAKOUT):
        assert decide("long",PositionState.LONG,regime=kind).decision is DecisionType.NO_ACTION
        assert decide("short",PositionState.SHORT,regime=kind).decision is DecisionType.NO_ACTION
    # A known, multi-dimensional reversal can invalidate an existing thesis under UNKNOWN.
    assert decide("short",PositionState.LONG,regime=Regime.UNKNOWN).decision is DecisionType.EXIT_LONG


def test_missing_or_invalid_optional_evidence_is_not_fabricated():
    missing=decide("long",evidence_changes={"normalized_ema_spread":None})
    assert missing.decision is DecisionType.NO_ACTION
    assert missing.reason_codes==("ANALYTICAL_EVIDENCE_INCOMPLETE",)
    assert missing.evidence["normalized_ema_spread"] is None
    invalid=decide("long",evidence_changes={"normalized_atr":D("-.01")})
    assert invalid.decision is DecisionType.NO_ACTION
    assert invalid.reason_codes==("ANALYTICAL_EVIDENCE_INVALID",)


def test_two_vs_three_exit_confirmation_setting_is_explicit():
    changes={"ema20_slope":D(".001"),"ema50_slope":D(".0005"),"confirmation_count":1,
             "candidate":Regime.BEAR_TREND,"retained_previous":True}
    assert decide("short",PositionState.LONG,regime=Regime.BULL_TREND,evidence_changes=changes).decision is DecisionType.EXIT_LONG
    stricter=AdaptiveTrendStrategy(AdaptiveTrendConfiguration(exit_confirmation_dimensions=3))
    result=decide("short",PositionState.LONG,strategy=stricter,regime=Regime.BULL_TREND,evidence_changes=changes)
    assert result.decision is DecisionType.NO_ACTION
    assert result.evidence["exit_confirmation_count"]==2


def test_inclusive_parameter_boundaries_and_strict_price_momentum_comparisons():
    for direction in ("long","short"):
        sign=D("1") if direction=="long" else D("-1")
        config=AdaptiveTrendConfiguration()
        result=decide(direction,evidence_changes={"normalized_ema_spread":sign*config.entry_min_ema_spread,
            "ema20_slope":sign*config.entry_fast_slope,"ema50_slope":sign*config.entry_slow_slope,
            "normalized_macd_histogram":sign*config.entry_min_normalized_histogram,
            "normalized_atr":config.max_normalized_atr,"volatility_ratio":config.max_volatility_ratio,
            "atr_ratio":config.max_atr_ratio},feature_changes={"realized_volatility":config.max_realized_volatility,"atr_14":D("2"),
                "ema_20":D("99.15") if direction=="long" else D("100.85"),
                "ema_50":D("99") if direction=="long" else D("101"),
                "macd":D(".5")*sign,"macd_signal":D(".49")*sign,"macd_histogram":D(".01")*sign})
        assert result.decision is (DecisionType.ENTER_LONG if direction=="long" else DecisionType.ENTER_SHORT)
    assert decide("long",feature_changes={"ema_20":D("100")}).decision is DecisionType.NO_ACTION


def test_no_lookahead_available_frame_only_and_no_state_mutation():
    strategy=AdaptiveTrendStrategy();frame=observation(strategy=strategy)
    first=strategy.decide(frame)
    later_view=replace(frame,as_of=AS_OF+timedelta(days=5))
    assert strategy.decide(later_view)==first
    with pytest.raises(ValueError): replace(frame,intelligence=replace(frame.intelligence,
        open_time=START+timedelta(hours=1),close_time=END+timedelta(hours=1)))
    with pytest.raises(TypeError): StrategyInput(frame.context,frame.candle,frame.intelligence,frame.regime,
        frame.definition,frame.as_of,future_candles=(frame.candle,))
    assert frame.position_state is PositionState.CASH
    assert strategy.decide(frame)==first
    assert frame==observation(strategy=strategy)


def test_thesis_state_identity_and_constructor_compatibility():
    original=observation("range")
    legacy=StrategyInput(original.context,original.candle,original.intelligence,original.regime,original.definition,original.as_of)
    assert legacy.position_state is PositionState.CASH
    assert legacy.input_identity==original.input_identity
    assert STRATEGY_INPUT_FORMAT=="strategy.input/v2"
    identities={replace(original,position_state=state).input_identity for state in PositionState}
    assert len(identities)==3
    for bad in ("LONG",None,1):
        with pytest.raises(ValueError): replace(original,position_state=bad)
    with pytest.raises(FrozenInstanceError): original.position_state=PositionState.LONG


def test_configuration_changes_affect_identity_without_optimization():
    normal=AdaptiveTrendStrategy()
    stricter=AdaptiveTrendStrategy(replace(normal.configuration,entry_min_ema_spread=D(".02")))
    a=decide(strategy=normal);b=decide(strategy=stricter)
    assert a.decision is DecisionType.ENTER_LONG and b.decision is DecisionType.NO_ACTION
    assert a.configuration_identity!=b.configuration_identity and a.decision_identity!=b.decision_identity
    reordered=StrategyDefinition("ADAPTIVE_TREND","1.0.0",dict(reversed(list(normal.definition.parameters.items()))))
    frame=replace(observation(strategy=normal),definition=reordered)
    assert normal.decide(frame)==a
    assert isinstance(normal,Strategy)
    with pytest.raises(ValueError): normal.decide(observation(strategy=stricter))


@pytest.mark.parametrize("value",[1.0,D("NaN"),D("-1"),D("0")])
def test_invalid_numeric_configuration(value):
    with pytest.raises(ValueError): AdaptiveTrendConfiguration(entry_min_ema_spread=value)


@pytest.mark.parametrize("params",[{"exit_confirmation_dimensions":1},{"exit_confirmation_dimensions":4},
    {"minimum_regime_confirmations":True},{"minimum_regime_confirmations":0},
    {"long_rsi_min":D("80")},{"short_rsi_max":D("60")},{"exit_short_rsi":D("100")}])
def test_configuration_constraints(params):
    with pytest.raises(ValueError): AdaptiveTrendConfiguration(**params)


def test_configuration_strategy_and_evidence_are_immutable():
    strategy=AdaptiveTrendStrategy()
    with pytest.raises(FrozenInstanceError): strategy.configuration.long_rsi_min=D("10")
    with pytest.raises(FrozenInstanceError): strategy.configuration=AdaptiveTrendConfiguration()
    result=decide(strategy=strategy)
    with pytest.raises(TypeError): result.evidence["entry_confirmations"]["regime"]=False
    with pytest.raises(TypeError): result.evidence["close"]=D("0")


def test_decimal_context_and_price_scale_do_not_change_rule_meaning():
    strategy=AdaptiveTrendStrategy();original=observation(strategy=strategy)
    assert decide(strategy=strategy)==decide(strategy=strategy)
    expected=strategy.decide(original)
    with localcontext() as context:
        context.prec=3
        assert strategy.decide(original)==expected
        assert strategy.decide(original).decision is DecisionType.ENTER_LONG
    for scale in (D("1000"),D(".000001")):
        candle=replace(original.candle,**{name:getattr(original.candle,name)*scale for name in ("open","high","low","close")})
        feature=replace(original.intelligence,**{name:getattr(original.intelligence,name)*scale for name in
            ("close","ema_20","ema_50","atr_14","macd","macd_signal","macd_histogram")})
        evidence=replace(original.regime.evidence,macd=feature.macd,macd_signal=feature.macd_signal,macd_histogram=feature.macd_histogram)
        scaled=replace(original,candle=candle,intelligence=feature,regime=replace(original.regime,evidence=evidence))
        assert strategy.decide(scaled).decision is DecisionType.ENTER_LONG


def test_no_execution_fields_or_automatic_opposite_entry():
    result=decide("short",PositionState.LONG)
    forbidden={"order","quantity","position_size","execution_authority","trade_intent","profit_probability","venue"}
    assert not forbidden & {item.name for item in fields(StrategyDecision)}
    assert not forbidden & set(result.evidence)
    assert result.decision is DecisionType.EXIT_LONG
    assert not hasattr(AdaptiveTrendStrategy(),"execute")


def test_fresh_process_has_no_provider_database_or_execution_dependencies():
    script="""import sys
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
assert AdaptiveTrendStrategy().definition.family == 'ADAPTIVE_TREND'
for prefix in ('httpx','sqlalchemy','fastapi','app.db','app.market_data','app.venues','app.auth','app.portfolios'):
    assert not any(name==prefix or name.startswith(prefix+'.') for name in sys.modules), prefix
"""
    result=subprocess.run([sys.executable,"-c",script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
