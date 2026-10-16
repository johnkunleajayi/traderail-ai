from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP, localcontext
import json
from uuid import UUID

import pytest
from app.market_intelligence.engine import calculate_features
from app.market_intelligence.types import Candle, DatasetContext, TIMEFRAME_SECONDS
from app.market_regimes.classification import Confirmation
from app.market_regimes.config import REGIME_CONFIG, RegimeConfiguration, candidate_first_index
from app.market_regimes.engine import classify_regimes
from app.market_regimes.types import Evidence, Regime

D = Decimal
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
ID = UUID(int=1)


def features(prices, symbol="BTC/USDT", timeframe="1h"):
    context = DatasetContext(ID, symbol, "BINANCE", timeframe)
    duration = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    with localcontext() as ctx:
        ctx.prec = 80
        source = [Candle(ID,"BINANCE",timeframe,START+i*duration,START+(i+1)*duration-timedelta(milliseconds=1),
            price,price*D("1.001"),price*D("0.999"),price,D(1),START+(i+1)*duration) for i,price in enumerate(prices)]
    return calculate_features(context,source,as_of=AS_OF)


def path(kind, count=120):
    with localcontext() as ctx:
        ctx.prec = 70
        if kind == "up": return [D(100)*D("1.003")**i for i in range(count)]
        if kind == "down": return [D(200)-D("0.2")*i-D("0.0001")*i*i for i in range(count)]
        if kind == "flat": return [D(100)]*count
        return [D(100)+D([0,1,2,1,0,-1,-2,-1][i%8])/D(100) for i in range(count)]


def run(rows, **kwargs): return classify_regimes(rows,as_of=AS_OF,**kwargs)


@pytest.mark.parametrize("kind,expected,reason",[("up",Regime.BULL_TREND,"TREND_UP_CONFIRMED"),
    ("down",Regime.BEAR_TREND,"TREND_DOWN_CONFIRMED"),("range",Regime.RANGE,"RANGE_STRUCTURE"),("flat",Regime.RANGE,"RANGE_STRUCTURE")])
def test_intentional_persistent_market_patterns(kind,expected,reason):
    result=run(features(path(kind)))
    assert result[-1].regime==expected
    assert reason in result[-1].evidence.reason_codes
    assert all(row.regime==expected for row in result[-10:])
    if kind=="flat":
        assert result[-1].evidence.normalized_atr==D("0.002")
        assert result[-1].evidence.volatility_ratio==0


@pytest.mark.parametrize("direction,level,reason",[("UP","103","BREAKOUT_UP"),("DOWN","97","BREAKOUT_DOWN")])
def test_departure_from_prior_closing_structure_with_momentum_and_volatility(direction,level,reason):
    prices=path("range",80)+[D(level)]
    result=run(features(prices));latest=result[-1]
    assert latest.regime==Regime.BREAKOUT
    assert latest.evidence.breakout_direction==direction and reason in latest.evidence.reason_codes
    # Reference excludes the departing candle, independently bounded by the quiet fixture's closes.
    assert D("99.98")<=latest.evidence.reference_low<=latest.evidence.reference_high<=D("100.02")
    assert latest.evidence.confirmation_required==1
    assert "BREAKOUT_VOLATILITY_CONFIRMED" in latest.evidence.reason_codes


def test_high_volatility_has_precedence_and_needs_two_observations():
    rows=features(path("range",80)+[D(110),D(90),D(115)])
    result=run(rows)
    assert result[-3].regime==Regime.UNKNOWN
    assert result[-3].evidence.candidate==Regime.HIGH_VOLATILITY
    assert result[-2].regime==Regime.HIGH_VOLATILITY
    assert result[-1].regime==Regime.HIGH_VOLATILITY
    assert "VOLATILITY_ELEVATED" in result[-1].evidence.reason_codes
    assert result[-1].evidence.confirmation_required==2
    assert result[-1].evidence.volatility_ratio>=REGIME_CONFIG.high_volatility_ratio


def test_price_departure_without_momentum_or_volatility_confirmation_is_not_breakout():
    rows=features(path("up"))
    assert rows[-1].rsi_14>70
    assert run(rows)[-1].regime==Regime.BULL_TREND
    falling=features(path("down"))
    assert falling[-1].rsi_14<30
    assert run(falling)[-1].regime==Regime.BEAR_TREND


def test_valid_conflicting_momentum_produces_unknown_not_unsafe():
    rows=features(path("up"))
    # Deliberately conflicting, finite and algebraically consistent measurement snapshot.
    rows[-1]=replace(rows[-1],rsi_14=D(40),macd=D(-1),macd_signal=D("-0.5"),macd_histogram=D("-0.5"))
    result=run(rows)[-1]
    assert result.regime==Regime.UNKNOWN
    assert "CONFLICTING_EVIDENCE" in result.evidence.reason_codes
    assert "DATA_UNSAFE" not in result.evidence.reason_codes


def test_computed_warmup_and_confirmation_are_explicit():
    assert candidate_first_index(REGIME_CONFIG)==52
    result=run(features(path("up",60)))
    assert all(row.regime==Regime.UNKNOWN for row in result[:54])
    assert result[51].evidence.reason_codes==("WARMUP_REQUIRED","INSUFFICIENT_EVIDENCE")
    assert result[52].evidence.confirmation_count==1
    assert result[53].evidence.confirmation_count==2
    assert result[54].regime==Regime.BULL_TREND
    assert result[54].evidence.available_candles==55
    assert result[54].evidence.candidate_history_required==53
    changed=replace(REGIME_CONFIG,slope_lookback=10,volatility_baseline_window=50)
    assert candidate_first_index(changed)==70
    assert run([])==[]


def test_confirmation_prevents_one_candle_direction_flip():
    state=Confirmation()
    up=Evidence(candidate=Regime.BULL_TREND,reason_codes=("TREND_UP_EVIDENCE",))
    down=Evidence(candidate=Regime.BEAR_TREND,reason_codes=("TREND_DOWN_EVIDENCE",))
    for _ in range(3): output,e=state.apply(Regime.BULL_TREND,up,REGIME_CONFIG)
    assert output==Regime.BULL_TREND
    output,e=state.apply(Regime.BEAR_TREND,down,REGIME_CONFIG)
    assert output==Regime.BULL_TREND and e.retained_previous and e.confirmation_count==1
    assert "PERSISTENCE_RETAINED" in e.reason_codes
    output,e=state.apply(Regime.BULL_TREND,up,REGIME_CONFIG)
    assert output==Regime.BULL_TREND
    for _ in range(3): output,e=state.apply(Regime.BEAR_TREND,down,REGIME_CONFIG)
    assert output==Regime.BEAR_TREND and e.confirmation_count==3
    output,e=state.apply(Regime.UNKNOWN,Evidence(reason_codes=("CONFLICTING_EVIDENCE",)),REGIME_CONFIG)
    assert output==Regime.UNKNOWN and state.established is None


@pytest.mark.parametrize("defect,reason",[("gap","INTERNAL_GAP"),("duplicate","DUPLICATE_TIMESTAMP"),("order","NON_CHRONOLOGICAL"),
    ("symbol","MIXED_CONTEXT"),("provider","MIXED_CONTEXT"),("timeframe","MIXED_CONTEXT"),
    ("missing","MISSING_REQUIRED_MEASUREMENT"),("price","NON_POSITIVE_PRICE"),("float","INVALID_NUMERIC_VALUE"),
    ("nan","INVALID_NUMERIC_VALUE"),("rsi","INVALID_RSI"),("atr","NEGATIVE_VOLATILITY"),("macd","INCONSISTENT_MACD"),
    ("timestamp","INVALID_TIMESTAMP"),("row","MALFORMED_ROW")])
def test_unsafe_prefix_quarantine_without_future_contamination(defect,reason):
    original=features(path("up"));rows=list(original);index=70
    if defect=="gap": rows.pop(index)
    elif defect=="duplicate": rows.insert(index,rows[index-1])
    elif defect=="order": rows[index],rows[index+1]=rows[index+1],rows[index];reason="INTERNAL_GAP"
    elif defect=="row": rows[index]=None
    else:
        patches={"symbol":{"canonical_symbol":"ETH/USDT"},"provider":{"provider":"QUIDAX"},"timeframe":{"timeframe":"4h"},
            "missing":{"ema_50":None},"price":{"close":D(0)},"float":{"close":1.1},"nan":{"realized_volatility":D("NaN")},
            "rsi":{"rsi_14":D(101)},"atr":{"atr_14":D(-1)},"macd":{"macd_histogram":D(999)},
            "timestamp":{"open_time":rows[index].open_time.replace(tzinfo=None)}}
        rows[index]=replace(rows[index],**patches[defect])
    result=run(rows)
    assert result[:index]==run(original[:index])
    assert result[index].regime==Regime.UNSAFE and reason in result[index].evidence.reason_codes
    assert all(row.regime==Regime.UNSAFE for row in result[index:])
    assert result[index+1].evidence.reason_codes==("DATA_UNSAFE","INVALID_PREFIX")


def test_reversed_input_and_duplicate_at_start_are_unsafe():
    rows=features(path("up",60))
    assert run(list(reversed(rows)))[1].evidence.reason_codes==("DATA_UNSAFE","NON_CHRONOLOGICAL")
    assert run([rows[0],rows[0]])[1].regime==Regime.UNSAFE


def test_incomplete_interval_and_non_positive_numeric_state_are_unsafe():
    rows=features(path("up",60))
    result=classify_regimes(rows,as_of=rows[-1].close_time)
    assert result[-1].regime==Regime.UNSAFE and "INCOMPLETE_CANDLE" in result[-1].evidence.reason_codes
    assert classify_regimes(rows,as_of=rows[-1].close_time+timedelta(milliseconds=1))[-1].regime!=Regime.UNSAFE
    for patch in ({"ema_20":D(0)},{"simple_return":D(-1)},{"realized_volatility":D(-1)}):
        invalid=rows[:-1]+[replace(rows[-1],**patch)]
        assert run(invalid)[-1].regime==Regime.UNSAFE


def test_no_lookahead_prefixes_and_future_shock():
    rows=features(path("up",100));full=run(rows)
    for count in (1,20,49,53,54,55,70,90): assert run(rows[:count])==full[:count]
    alternate=features(path("up",80)+[D(200),D(50),D(300)])
    assert run(alternate)[:80]==full[:80]
    gapped=list(rows);gapped.pop(90)
    assert run(gapped)[:90]==full[:90]


def test_scale_normalization_is_consistent_across_price_levels():
    original=path("up")
    reference=run(features(original))
    for scale in (D("1000.12345678"),D("0.000000000001")):
        with localcontext() as ctx:
            ctx.prec=80
            prices=[value*scale for value in original]
        result=run(features(prices,symbol="ETH/USDT"))
        assert [row.regime for row in result]==[row.regime for row in reference]
        for field in ("normalized_ema_spread","ema20_slope","ema50_slope","normalized_atr","volatility_ratio","atr_ratio"):
            assert abs(getattr(result[-1].evidence,field)-getattr(reference[-1].evidence,field))<D("1e-40")


def test_repeatability_immutable_inputs_and_configuration_identity():
    rows=features(path("up"));before=[asdict(row) for row in rows];expected=run(rows)
    with localcontext() as ctx:
        ctx.prec=8;ctx.rounding=ROUND_UP
        assert run(rows)==expected
    assert [asdict(row) for row in rows]==before
    assert expected[-1].configuration==REGIME_CONFIG
    assert expected[-1].configuration.engine_version=="1.0.0"
    assert expected[-1].feature_engine_version=="1.0.0"
    with pytest.raises(FrozenInstanceError): expected[-1].regime=Regime.RANGE
    with pytest.raises(FrozenInstanceError): REGIME_CONFIG.persistence_window=1


def test_output_vocabulary_has_no_trade_decisions_or_advice():
    result=run(features(path("up")))
    assert {regime.value for regime in Regime}=={"BULL_TREND","BEAR_TREND","RANGE","BREAKOUT","HIGH_VOLATILITY","UNSAFE","UNKNOWN"}
    text=json.dumps([asdict(row) for row in result],default=str).lower()
    for forbidden in ('"buy"','"sell"','"hold"','recommendation','profit','confidence','position_size','tradeintent','score'):
        assert forbidden not in text
    assert "TREND_UP_CONFIRMED" in result[-1].evidence.reason_codes
    assert result[-1].evidence.normalized_ema_spread>0
    assert result[-1].evidence.rsi_14==100


@pytest.mark.parametrize("timeframe",TIMEFRAME_SECONDS)
def test_all_current_completed_timeframes_are_supported(timeframe):
    result=run(features(path("up",60),timeframe=timeframe))
    assert result[-1].regime==Regime.BULL_TREND and result[-1].timeframe==timeframe


def test_bad_configuration_and_completion_boundary_are_not_market_uncertainty():
    with pytest.raises(ValueError): replace(REGIME_CONFIG,persistence_window=0)
    with pytest.raises(ValueError): replace(REGIME_CONFIG,trend_spread=D("NaN"))
    with pytest.raises(ValueError): replace(REGIME_CONFIG,momentum_down_rsi=D(80))
    with pytest.raises(ValueError): classify_regimes([],as_of=AS_OF.replace(tzinfo=None))


def test_shrinking_absolute_momentum_in_percentage_decline_is_explicit_unknown():
    with localcontext() as ctx:
        ctx.prec=70
        rows=features([D(100)*D("0.997")**i for i in range(120)])
    result=run(rows)[-1]
    assert result.regime==Regime.UNKNOWN
    assert result.evidence.normalized_ema_spread<0 and result.evidence.ema20_slope<0
    assert result.evidence.rsi_14==0 and result.evidence.macd<0 and result.evidence.macd_histogram>0
    assert "CONFLICTING_EVIDENCE" in result.evidence.reason_codes


def test_small_departure_lacks_volatility_confirmation_and_remains_unknown():
    result=run(features(path("range",80)+[D("100.3")]))[-1]
    assert result.regime==Regime.UNKNOWN
    assert result.evidence.breakout_direction is None
    assert result.evidence.volatility_ratio<REGIME_CONFIG.breakout_volatility_ratio
    assert result.evidence.atr_ratio<REGIME_CONFIG.breakout_atr_ratio


def test_linear_prices_have_independently_known_normalized_structure():
    rows=features([D(100+i) for i in range(120)])
    e=run(rows)[-1].evidence
    with localcontext() as ctx:
        ctx.prec=80
        assert abs(e.normalized_ema_spread-D(15)/D(219))<D("1e-45")
        assert abs(e.ema20_slope-D(1)/D(219))<D("1e-45")
        assert abs(e.ema50_slope-D(1)/D(219))<D("1e-45")
    assert e.reference_high==D(218) and e.reference_low==D(199)


def test_unsafe_overrides_even_an_established_volatility_episode():
    rows=features(path("range",80)+[D(110),D(90),D(115)])
    rows[-1]=replace(rows[-1],close=D(0))
    result=run(rows)
    assert result[-2].regime==Regime.HIGH_VOLATILITY
    assert result[-1].regime==Regime.UNSAFE and "NON_POSITIVE_PRICE" in result[-1].evidence.reason_codes


def test_aware_offset_input_normalizes_to_utc_without_changing_results():
    rows=features(path("up",60));offset=timezone(timedelta(hours=1))
    converted=[replace(row,open_time=row.open_time.astimezone(offset),close_time=row.close_time.astimezone(offset)) for row in rows]
    assert run(converted)==run(rows)
    assert run(converted)[-1].open_time.tzinfo==timezone.utc


def test_threshold_types_are_decimal_and_version_is_explicit():
    with pytest.raises(ValueError): replace(REGIME_CONFIG,trend_spread=1)
    with pytest.raises(ValueError): replace(REGIME_CONFIG,engine_version=1)
