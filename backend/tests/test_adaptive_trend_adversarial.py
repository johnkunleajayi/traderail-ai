"""Adversarial v1 contract tests: no fitting, strategy execution loop or real observations."""
from collections.abc import Mapping
from dataclasses import asdict, fields, replace
from datetime import timedelta
from decimal import Context, Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext
from itertools import product
from pathlib import Path
import ast
import socket
import subprocess
import sys
import urllib.request
import pytest
from app.market_intelligence.engine import calculate_features
from app.market_intelligence.types import Candle
from app.market_regimes.engine import classify_regimes
from app.market_regimes.types import Regime
from app.strategies.configuration import StrategyDefinition
from app.strategies.contract import evaluate_strategy
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.inputs import PositionState, StrategyInput
from app.strategies.adaptive_trend import AdaptiveTrendConfiguration, AdaptiveTrendStrategy
from test_adaptive_trend import observation, START, AS_OF, CONTEXT

D=Decimal
BOUNDARY_CONTEXT=Context(prec=50,rounding=ROUND_HALF_EVEN)
CONFIG=AdaptiveTrendConfiguration()
SCENARIOS=[
    ("clean_bull",{},DecisionType.ENTER_LONG,"LONG_TREND_CONFIRMED"),
    ("clean_bear",{"direction":"short"},DecisionType.ENTER_SHORT,"SHORT_TREND_CONFIRMED"),
    ("weak_bull",{"feature_changes":{"rsi_14":D("55"),"macd_signal":D(".995"),"macd_histogram":D(".005")},
        "evidence_changes":{"normalized_macd_histogram":D(".00005")}},DecisionType.NO_ACTION,"MOMENTUM_NOT_CONFIRMED"),
    ("weak_bear",{"direction":"short","feature_changes":{"rsi_14":D("45"),"macd_signal":D("-.995"),"macd_histogram":D("-.005")},
        "evidence_changes":{"normalized_macd_histogram":D("-.00005")}},DecisionType.NO_ACTION,"MOMENTUM_NOT_CONFIRMED"),
    ("flat_range",{"direction":"range"},DecisionType.NO_ACTION,"REGIME_NOT_TRENDING"),
    ("breakout_not_established",{"regime":Regime.BREAKOUT},DecisionType.NO_ACTION,"REGIME_NOT_TRENDING"),
    ("conflicting_rsi",{"feature_changes":{"rsi_14":D("40")}},DecisionType.NO_ACTION,"MOMENTUM_NOT_CONFIRMED"),
    ("conflicting_macd",{"feature_changes":{"macd":D("-1"),"macd_signal":D("-.5"),"macd_histogram":D("-.5")},
        "evidence_changes":{"normalized_macd_histogram":D("-.005")}},DecisionType.NO_ACTION,"MOMENTUM_NOT_CONFIRMED"),
    ("insufficient_spread",{"feature_changes":{"ema_20":D("99.1"),"ema_50":D("99")},
        "evidence_changes":{"normalized_ema_spread":D(".001")}},DecisionType.NO_ACTION,"EMA_STRUCTURE_NOT_CONFIRMED"),
    ("weak_slopes",{"evidence_changes":{"ema20_slope":D(".0001"),"ema50_slope":D(".00005")}},DecisionType.NO_ACTION,"EMA_SLOPES_NOT_CONFIRMED"),
    ("excessive_volatility",{"feature_changes":{"realized_volatility":D(".03")}},DecisionType.NO_ACTION,"VOLATILITY_TOO_HIGH"),
    ("baseline_shock",{"evidence_changes":{"volatility_ratio":D("3")}},DecisionType.NO_ACTION,"VOLATILITY_TOO_HIGH"),
    ("retained_regime",{"evidence_changes":{"candidate":Regime.RANGE,"confirmation_count":1,"retained_previous":True,
        "previous_regime":Regime.BULL_TREND}},DecisionType.NO_ACTION,"REGIME_NOT_CONFIRMED"),
    ("short_streak",{"evidence_changes":{"confirmation_count":2}},DecisionType.NO_ACTION,"REGIME_NOT_CONFIRMED"),
    ("incomplete_evidence",{"evidence_changes":{"normalized_atr":None}},DecisionType.NO_ACTION,"ANALYTICAL_EVIDENCE_INCOMPLETE"),
    ("healthy_long",{"state":PositionState.LONG},DecisionType.NO_ACTION,"LONG_EXIT_NOT_CONFIRMED"),
    ("healthy_short",{"direction":"short","state":PositionState.SHORT},DecisionType.NO_ACTION,"SHORT_EXIT_NOT_CONFIRMED"),
    ("partial_long",{"state":PositionState.LONG,"feature_changes":{"rsi_14":D("45"),"macd":D("-1"),
        "macd_signal":D("-.5"),"macd_histogram":D("-.5")},"evidence_changes":{"normalized_macd_histogram":D("-.005")}},
        DecisionType.NO_ACTION,"LONG_EXIT_NOT_CONFIRMED"),
    ("partial_short",{"direction":"short","state":PositionState.SHORT,"feature_changes":{"rsi_14":D("55"),
        "macd":D("1"),"macd_signal":D(".5"),"macd_histogram":D(".5")},"evidence_changes":{"normalized_macd_histogram":D(".005")}},
        DecisionType.NO_ACTION,"SHORT_EXIT_NOT_CONFIRMED"),
    ("invalidated_long",{"direction":"short","state":PositionState.LONG,"regime":Regime.BULL_TREND,
        "evidence_changes":{"candidate":Regime.BEAR_TREND,"confirmation_count":1,"retained_previous":True}},
        DecisionType.EXIT_LONG,"LONG_TREND_INVALIDATED"),
    ("invalidated_short",{"state":PositionState.SHORT,"regime":Regime.BEAR_TREND,
        "evidence_changes":{"candidate":Regime.BULL_TREND,"confirmation_count":1,"retained_previous":True}},
        DecisionType.EXIT_SHORT,"SHORT_TREND_INVALIDATED"),
    ("opposite_while_long",{"direction":"short","state":PositionState.LONG},DecisionType.EXIT_LONG,"OPPOSITE_TREND_CONFIRMED"),
    ("opposite_while_short",{"state":PositionState.SHORT},DecisionType.EXIT_SHORT,"OPPOSITE_TREND_CONFIRMED"),
]


def run(frame,strategy=None):
    return evaluate_strategy(strategy or AdaptiveTrendStrategy(),frame)


def allowed(state):
    return {PositionState.CASH:{DecisionType.NO_ACTION,DecisionType.ENTER_LONG,DecisionType.ENTER_SHORT},
        PositionState.LONG:{DecisionType.NO_ACTION,DecisionType.EXIT_LONG},
        PositionState.SHORT:{DecisionType.NO_ACTION,DecisionType.EXIT_SHORT}}[state]


@pytest.mark.parametrize("name,kwargs,expected,reason",SCENARIOS,ids=[row[0] for row in SCENARIOS])
def test_named_adversarial_conditions(name,kwargs,expected,reason):
    frame=observation(**kwargs)
    first=run(frame)
    assert first.decision is expected
    assert reason in first.reason_codes
    assert first.decision in allowed(frame.position_state)
    assert first==run(frame) and first.decision_identity==run(frame).decision_identity
    assert frame==observation(**kwargs)
    if name.startswith("partial_"):
        assert first.evidence["exit_confirmation_count"]==1
    if name.startswith("invalidated_"):
        assert first.evidence["exit_confirmation_count"]==3
        assert not first.evidence["opposite_trend_confirmed"]


def adjacent(value,offset):
    return value.next_minus(BOUNDARY_CONTEXT) if offset<0 else value.next_plus(BOUNDARY_CONTEXT) if offset>0 else value


def signed(value,negative):
    return value.copy_negate() if negative else value


def update_frame(frame,features=None,evidence=None):
    f=replace(frame.intelligence,**(features or {}))
    mirror={name:getattr(f,name) for name in ("rsi_14","macd","macd_signal","macd_histogram","realized_volatility")}
    e=replace(frame.regime.evidence,**{**mirror,**(evidence or {})})
    return replace(frame,intelligence=f,regime=replace(frame.regime,evidence=e),candle=replace(frame.candle,close=f.close))


ENTRY_THRESHOLDS=[("spread","entry_min_ema_spread","structure"),("fast_slope","entry_fast_slope","slopes"),
    ("slow_slope","entry_slow_slope","slopes"),("histogram","entry_min_normalized_histogram","momentum"),
    ("atr","max_normalized_atr","volatility"),("realized","max_realized_volatility","volatility"),
    ("volatility_ratio","max_volatility_ratio","volatility"),("atr_ratio","max_atr_ratio","volatility")]


@pytest.mark.parametrize("direction",["long","short"])
@pytest.mark.parametrize("offset",[-1,0,1],ids=["adjacent_below","exact","adjacent_above"])
@pytest.mark.parametrize("dimension,parameter,flag",ENTRY_THRESHOLDS,ids=[row[0] for row in ENTRY_THRESHOLDS])
def test_entry_decimal_boundaries(direction,offset,dimension,parameter,flag):
    frame=observation(direction);value=adjacent(getattr(CONFIG,parameter),offset)
    negative=direction=="short";f={};e={}
    with localcontext(Context(prec=100)):
        if dimension=="spread":
            e["normalized_ema_spread"]=signed(value,negative)
            f["ema_50"]=D("101") if negative else D("99")
            f["ema_20"]=f["ema_50"]+signed(value*D("100"),negative)
        elif dimension in ("fast_slope","slow_slope"):
            e["ema20_slope" if dimension=="fast_slope" else "ema50_slope"]=signed(value,negative)
        elif dimension=="histogram":
            histogram=signed(value*D("100"),negative)
            f.update(macd=histogram,macd_signal=D("0"),macd_histogram=histogram)
            e["normalized_macd_histogram"]=signed(value,negative)
        elif dimension=="atr":
            e["normalized_atr"]=value;f["atr_14"]=value*D("100")
        elif dimension=="realized": f["realized_volatility"]=value
        else: e[dimension]=value
    result=run(update_frame(frame,f,e))
    passes=offset<=0 if dimension in ("atr","realized","volatility_ratio","atr_ratio") else offset>=0
    expected=(DecisionType.ENTER_SHORT if negative else DecisionType.ENTER_LONG) if passes else DecisionType.NO_ACTION
    assert result.decision is expected
    assert result.evidence["entry_confirmations"][flag] is passes
    assert all(passed for name,passed in result.evidence["entry_confirmations"].items() if name!=flag)
    if offset:
        assert value!=getattr(CONFIG,parameter)  # No rounding away adjacent observations.


@pytest.mark.parametrize("direction",["long","short"])
@pytest.mark.parametrize("endpoint",["min","max"])
@pytest.mark.parametrize("offset",[-1,0,1])
def test_inclusive_rsi_bands_and_adjacent_values(direction,endpoint,offset):
    value=adjacent(getattr(CONFIG,direction+"_rsi_"+endpoint),offset)
    result=run(update_frame(observation(direction),{"rsi_14":value}))
    passes=offset>=0 if endpoint=="min" else offset<=0
    expected=(DecisionType.ENTER_LONG if direction=="long" else DecisionType.ENTER_SHORT) if passes else DecisionType.NO_ACTION
    assert result.decision is expected
    assert result.evidence["entry_confirmations"]["momentum"] is passes


def exit_boundary_frame(state,dimension,value):
    long=state==PositionState.LONG
    frame=observation("short" if long else "long",state,
        regime=Regime.BULL_TREND if long else Regime.BEAR_TREND,
        evidence_changes={"candidate":Regime.BEAR_TREND if long else Regime.BULL_TREND,
            "confirmation_count":1,"retained_previous":True})
    f={};e={}
    with localcontext(Context(prec=100)):
        if dimension=="spread":
            # Momentum anchors one dimension; slopes cannot conceal structure's boundary.
            e.update(ema20_slope=D("0"),ema50_slope=D("0"),normalized_ema_spread=signed(value,long))
            f["ema_50"]=D("101") if long else D("99")
            f["ema_20"]=f["ema_50"]+signed(value*D("100"),long)
        elif dimension in ("fast_slope","slow_slope"):
            # Original EMA ordering suppresses structure; close is beyond fast EMA.
            f.update(ema_20=D("102") if long else D("98"),ema_50=D("101") if long else D("99"))
            e["normalized_ema_spread"]=D(".01") if long else D("-.01")
            e["ema20_slope" if dimension=="fast_slope" else "ema50_slope"]=signed(value,long)
        else:
            # Reversed structure anchors one dimension, slopes are disabled.
            e.update(ema20_slope=D("0"),ema50_slope=D("0"))
            if dimension=="histogram":
                histogram=signed(value*D("100"),long)
                f.update(macd=histogram,macd_signal=D("0"),macd_histogram=histogram)
                e["normalized_macd_histogram"]=signed(value,long)
            else: f["rsi_14"]=value
    return update_frame(frame,f,e)


EXIT_THRESHOLDS=[("spread","exit_min_ema_spread","structure"),("fast_slope","exit_fast_slope","slopes"),
    ("slow_slope","exit_slow_slope","slopes"),("histogram","exit_min_normalized_histogram","momentum")]


@pytest.mark.parametrize("state",[PositionState.LONG,PositionState.SHORT])
@pytest.mark.parametrize("offset",[-1,0,1])
@pytest.mark.parametrize("dimension,parameter,flag",EXIT_THRESHOLDS,ids=[row[0] for row in EXIT_THRESHOLDS])
def test_exit_boundaries_cannot_be_hidden_by_other_dimensions(state,offset,dimension,parameter,flag):
    value=adjacent(getattr(CONFIG,parameter),offset)
    result=run(exit_boundary_frame(state,dimension,value))
    passes=offset>=0
    assert result.evidence["exit_confirmations"][flag] is passes
    assert result.evidence["exit_confirmation_count"]==(2 if passes else 1)
    assert not result.evidence["opposite_trend_confirmed"]
    expected=(DecisionType.EXIT_LONG if state==PositionState.LONG else DecisionType.EXIT_SHORT) if passes else DecisionType.NO_ACTION
    assert result.decision is expected


@pytest.mark.parametrize("state",[PositionState.LONG,PositionState.SHORT])
@pytest.mark.parametrize("offset",[-1,0,1])
def test_exit_rsi_boundaries(state,offset):
    threshold=CONFIG.exit_long_rsi if state==PositionState.LONG else CONFIG.exit_short_rsi
    result=run(exit_boundary_frame(state,"rsi",adjacent(threshold,offset)))
    passes=offset<=0 if state==PositionState.LONG else offset>=0
    assert result.evidence["exit_confirmations"]["momentum"] is passes
    assert result.evidence["exit_confirmation_count"]==(2 if passes else 1)
    assert result.decision is ((DecisionType.EXIT_LONG if state==PositionState.LONG else DecisionType.EXIT_SHORT) if passes else DecisionType.NO_ACTION)


@pytest.mark.parametrize("direction",["long","short"])
@pytest.mark.parametrize("offset",[-1,0,1])
def test_price_must_be_strictly_beyond_fast_ema(direction,offset):
    frame=observation(direction)
    fast=adjacent(frame.intelligence.close,offset)
    with localcontext(Context(prec=100)):
        spread=(fast-frame.intelligence.ema_50)/frame.intelligence.close
    result=run(update_frame(frame,{"ema_20":fast},{"normalized_ema_spread":spread}))
    passes=offset<0 if direction=="long" else offset>0
    assert result.evidence["entry_confirmations"]["close_position"] is passes
    assert result.decision is ((DecisionType.ENTER_LONG if direction=="long" else DecisionType.ENTER_SHORT) if passes else DecisionType.NO_ACTION)


@pytest.mark.parametrize("count",[2,3,4])
def test_confirmation_integer_boundary(count):
    frame=observation(evidence_changes={"confirmation_count":count})
    assert run(frame).decision is (DecisionType.ENTER_LONG if count>=3 else DecisionType.NO_ACTION)


MATRIX={
    Regime.BULL_TREND:(DecisionType.ENTER_LONG,DecisionType.NO_ACTION,DecisionType.EXIT_SHORT),
    Regime.BEAR_TREND:(DecisionType.ENTER_SHORT,DecisionType.EXIT_LONG,DecisionType.NO_ACTION),
    Regime.RANGE:(DecisionType.NO_ACTION,)*3,
    Regime.UNKNOWN:(DecisionType.NO_ACTION,)*3,
    # Upward breakout snapshot: no established entry, but existing SHORT is invalidated by structure+momentum.
    Regime.BREAKOUT:(DecisionType.NO_ACTION,DecisionType.NO_ACTION,DecisionType.EXIT_SHORT),
    Regime.HIGH_VOLATILITY:(DecisionType.NO_ACTION,)*3,
    Regime.UNSAFE:(DecisionType.NO_ACTION,)*3,
}


@pytest.mark.parametrize("kind",list(Regime))
@pytest.mark.parametrize("state",list(PositionState))
def test_complete_position_regime_matrix(kind,state):
    direction="long" if kind in (Regime.BULL_TREND,Regime.BREAKOUT,Regime.HIGH_VOLATILITY,Regime.UNSAFE) else "short" if kind==Regime.BEAR_TREND else "range"
    e={"ema20_slope":D("0"),"ema50_slope":D("0")} if kind==Regime.BREAKOUT else {}
    frame=observation(direction,state,regime=kind,evidence_changes=e)
    result=run(frame)
    assert result.decision is MATRIX[kind][list(PositionState).index(state)]
    assert result.decision in allowed(state)
    assert frame.position_state is state
    assert result.evidence["position_state"]==state.value


def test_cross_product_cannot_duplicate_entries_exit_cash_or_reverse_positions():
    for state,kind,direction in product(PositionState,Regime,("long","short","range")):
        frame=observation(direction,state,regime=kind)
        result=run(frame)
        assert result.decision in allowed(state),(state,kind,direction,result.decision)
        assert frame.position_state is state


def equivalent_decimal(value):
    text=format(value,"f")
    return D(text+"000" if "." in text else text+".000")


def equivalent_dataclass(record):
    return replace(record,**{item.name:equivalent_decimal(getattr(record,item.name)) for item in fields(record)
                              if isinstance(getattr(record,item.name),D)})


def test_dictionary_order_and_decimal_representations_preserve_full_identity():
    strategy=AdaptiveTrendStrategy();frame=observation(strategy=strategy)
    original=run(frame,strategy)
    parameters={key:equivalent_decimal(value) if isinstance(value,D) else value
                for key,value in reversed(list(strategy.definition.parameters.items()))}
    definition=StrategyDefinition("ADAPTIVE_TREND","1.0.0",parameters)
    regime=replace(frame.regime,evidence=equivalent_dataclass(frame.regime.evidence),
                   configuration=equivalent_dataclass(frame.regime.configuration))
    equivalent=replace(frame,definition=definition,candle=equivalent_dataclass(frame.candle),
                       intelligence=equivalent_dataclass(frame.intelligence),regime=regime)
    assert definition.configuration_identity==frame.definition.configuration_identity
    assert equivalent.input_identity==frame.input_identity
    assert run(equivalent,strategy)==original


@pytest.mark.parametrize("parameter",[item.name for item in fields(AdaptiveTrendConfiguration)])
def test_every_meaningful_parameter_changes_identity_without_selecting_better_settings(parameter):
    strategy=AdaptiveTrendStrategy();original=run(observation(strategy=strategy),strategy)
    current=getattr(strategy.configuration,parameter)
    with localcontext(Context(prec=100)):
        changed=current+1 if isinstance(current,int) else current+D("1e-20")
    alternate=AdaptiveTrendStrategy(replace(strategy.configuration,**{parameter:changed}))
    result=run(observation(strategy=alternate),alternate)
    assert result.configuration_identity!=original.configuration_identity
    assert result.input_identity!=original.input_identity
    assert result.decision_identity!=original.decision_identity
    # No profitability comparison or selection of changed settings is performed.


def test_position_state_changes_identity_even_when_all_decisions_are_no_action():
    frame=observation("range")
    results=[run(replace(frame,position_state=state)) for state in PositionState]
    assert all(result.decision is DecisionType.NO_ACTION for result in results)
    assert len({result.input_identity for result in results})==3
    assert len({result.decision_identity for result in results})==3


@pytest.mark.parametrize("precision,rounding",[(3,ROUND_DOWN),(28,ROUND_HALF_EVEN),(80,ROUND_UP)])
def test_repeatability_in_different_ambient_decimal_contexts(precision,rounding):
    frames=[observation(**kwargs) for _,kwargs,_,_ in SCENARIOS]
    baseline=[run(frame) for frame in frames]
    with localcontext(Context(prec=precision,rounding=rounding)):
        assert [run(frame) for frame in frames]==baseline
        assert [run(frame).decision_identity for frame in frames]==[item.decision_identity for item in baseline]


def test_appended_future_observations_do_not_rewrite_prior_decision():
    strategy=AdaptiveTrendStrategy();at_t=observation(strategy=strategy)
    before=run(at_t,strategy)
    observations=[at_t]
    for i,direction in enumerate(("short","range","long","short"),start=1):
        frame=observation(direction,strategy=strategy)
        delta=timedelta(hours=i)
        frame=replace(frame,candle=replace(frame.candle,open_time=frame.candle.open_time+delta,
            close_time=frame.candle.close_time+delta,retrieved_at=frame.candle.retrieved_at+delta),
            intelligence=replace(frame.intelligence,open_time=frame.intelligence.open_time+delta,close_time=frame.intelligence.close_time+delta),
            regime=replace(frame.regime,open_time=frame.regime.open_time+delta,close_time=frame.regime.close_time+delta),as_of=AS_OF+delta)
        observations.append(frame)
        run(frame,strategy)
        assert run(observations[0],strategy)==before
    assert observations[0] is at_t
    assert at_t.position_state is PositionState.CASH


def test_real_engine_prefix_is_causal_when_extreme_future_candles_are_appended():
    candles=[]
    for i in range(130):
        value=D("100")+D(i) if i<100 else D("2000") if i%2 else D("10")
        start=START+timedelta(hours=i)
        candles.append(Candle(CONTEXT.instrument_id,"BINANCE","1h",start,start+timedelta(hours=1,milliseconds=-1),
            value,value+D("1"),value-D("1"),value,D("1"),start+timedelta(hours=1)))
    through_t=candles[:100];boundary=through_t[-1].retrieved_at
    prefix_features=calculate_features(CONTEXT,through_t,as_of=boundary)
    prefix_regimes=classify_regimes(prefix_features,as_of=boundary)
    full_features=calculate_features(CONTEXT,candles,as_of=candles[-1].retrieved_at)
    full_regimes=classify_regimes(full_features,as_of=candles[-1].retrieved_at)
    assert prefix_features==full_features[:100] and prefix_regimes==full_regimes[:100]
    strategy=AdaptiveTrendStrategy()
    for index in (60,80,99):
        frame=StrategyInput(CONTEXT,through_t[index],prefix_features[index],prefix_regimes[index],strategy.definition,
                            through_t[index].retrieved_at,PositionState.CASH)
        with_future=replace(frame,intelligence=full_features[index],regime=full_regimes[index])
        assert run(frame,strategy)==run(with_future,strategy)
        with pytest.raises(ValueError): replace(frame,intelligence=full_features[index+1])


def test_external_access_is_blocked_during_all_scenario_decisions(monkeypatch):
    import httpx
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    from app.db import session as database
    from app.market_data.binance import BinanceHistoricalProvider
    from app.market_data.quidax import QuidaxPublicProvider
    frames=[observation(**kwargs) for _,kwargs,_,_ in SCENARIOS]
    def forbidden(*args,**kwargs):
        raise AssertionError("Strategy attempted an external/provider/database action")
    for owner,name in ((socket,"create_connection"),(socket.socket,"connect"),(urllib.request,"urlopen"),
        (httpx.Client,"send"),(httpx.AsyncClient,"send"),(Engine,"connect"),(Session,"execute"),
        (Session,"connection"),(Session,"commit"),(Session,"flush"),(Session,"add"),
        (database,"get_engine"),(database,"get_session"),(BinanceHistoricalProvider,"fetch_candles"),(QuidaxPublicProvider,"fetch_ticker")):
        monkeypatch.setattr(owner,name,forbidden)
    for frame in frames:
        assert run(frame).decision in allowed(frame.position_state)


def test_source_audit_no_venue_order_sizing_balance_or_pnl_behavior():
    root=Path(__file__).resolve().parents[1]/"app/strategies/adaptive_trend"
    prohibited_roots=("httpx","requests","socket","urllib","sqlalchemy","fastapi","app.db","app.market_data","app.venues","app.portfolios")
    forbidden_attributes={"order","orders","place_order","submit_order","execute","trade_intent","quantity",
        "notional","position_size","size_position","get_balance","balance","pnl","portfolio","account"}
    for path in root.glob("*.py"):
        source=path.read_text(encoding="utf-8");tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                imports=[alias.name for alias in node.names] if isinstance(node,ast.Import) else [node.module or ""]
                assert not any(name==prefix or name.startswith(prefix+".") for name in imports for prefix in prohibited_roots)
            if isinstance(node,ast.Attribute): assert node.attr not in forbidden_attributes
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                assert node.func.id not in {"open","eval","exec","__import__","TradeIntent","Order","RiskEngine","PositionSizer",
                    "place_order","submit_order","size_position","get_balance"}
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                assert not any(venue in node.value.upper() for venue in ("QUIDAX","BINANCE"))
    frame=observation();result=run(frame)
    assert not forbidden_attributes&set(result.evidence)
    with pytest.raises(TypeError): StrategyInput(frame.context,frame.candle,frame.intelligence,frame.regime,
        frame.definition,frame.as_of,balance=D("10000"),pnl=D("100"))


def test_fresh_strategy_import_does_not_load_external_dependencies():
    script="""import sys
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
assert AdaptiveTrendStrategy().definition.version=='1.0.0'
for prefix in ('httpx','sqlalchemy','fastapi','app.db','app.market_data','app.venues','app.portfolios'):
    assert not any(name==prefix or name.startswith(prefix+'.') for name in sys.modules),prefix
"""
    result=subprocess.run([sys.executable,"-c",script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_decision_evidence_dictionary_order_cannot_change_identity():
    frame=observation();original=run(frame)
    def reorder(value):
        if isinstance(value,Mapping):
            return {key:reorder(item) for key,item in reversed(list(value.items()))}
        if isinstance(value,tuple): return tuple(reorder(item) for item in value)
        return value
    reordered=StrategyDecision(frame,original.decision,original.reason_codes,reorder(original.evidence))
    assert reordered==original
    assert reordered.decision_identity==original.decision_identity


@pytest.mark.parametrize("state",[PositionState.LONG,PositionState.SHORT])
def test_confirmed_opposite_branch_is_reachable_without_all_three_deterioration_dimensions(state):
    strategy=AdaptiveTrendStrategy(AdaptiveTrendConfiguration(exit_confirmation_dimensions=3))
    long=state==PositionState.LONG
    frame=observation("short" if long else "long",state,strategy=strategy,
        feature_changes={"macd":D("-1") if long else D("1"),
            "macd_signal":D("-1") if long else D("1"),"macd_histogram":D("0")},
        evidence_changes={"normalized_macd_histogram":D("0")})
    result=run(frame,strategy)
    assert result.evidence["exit_confirmation_count"]==2
    assert result.evidence["exit_confirmation_required"]==3
    assert result.evidence["opposite_trend_confirmed"] is True
    assert result.decision is (DecisionType.EXIT_LONG if long else DecisionType.EXIT_SHORT)
    assert "OPPOSITE_TREND_CONFIRMED" in result.reason_codes


def test_success_and_failure_paths_are_reachable_without_changing_defaults():
    decisions={run(observation(**kwargs)).decision for _,kwargs,_,_ in SCENARIOS}
    assert decisions==set(DecisionType)
    quality=run(observation(evidence_changes={"normalized_atr":D("-.01")}))
    assert quality.reason_codes==("ANALYTICAL_EVIDENCE_INVALID",)
    for kind,reason in ((Regime.UNSAFE,"DATA_UNSAFE"),(Regime.HIGH_VOLATILITY,"HIGH_VOLATILITY_REGIME")):
        result=run(observation(regime=kind,evidence_changes={"normalized_atr":None}))
        assert result.decision is DecisionType.NO_ACTION and result.reason_codes==(reason,)
